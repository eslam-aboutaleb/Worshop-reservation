"""In-process pub/sub for Server-Sent Events.

Each connected SSE client owns an ``asyncio.Queue``. The
reservation service calls :func:`publish` on every
create/cancel; the queue gets a copy of the event payload;
the SSE generator yields it as a ``data: ...\\n\\n`` message.
The single-channel design lets the frontend open one
``EventSource`` and filter by ``workshop_id`` on each event
payload.

Why two subscriber sets?
------------------------

* :data:`_global_subscribers` receives every workshop's
  events. This is what the public SSE endpoint
  (``GET /api/workshops/events``) uses so the browser can
  render live spot counts on the list page.
* :data:`_workshop_subscribers` is reserved for per-workshop
  channels. No per-workshop endpoint is exposed today, but
  the data structure is in place if/when one is added.

Concurrency
-----------

All subscriber-set mutations happen under :data:`_lock`; the
``publish`` snapshot is taken under the lock and then
iterated without it so a slow subscriber cannot stall the
producer. ``put_nowait`` on a full queue is dropped (logged
at debug level) so a stuck client cannot back up the event
loop; the next user action will re-fetch the canonical state
via the workshop detail endpoint.

Scaling past one replica
------------------------

This module is in-process and does not cross replica
boundaries. A multi-replica deployment uses the Redis
adapter (see :mod:`ws_core.realtime.redis`, added in the
realtime plan); the public surface
(``subscribe_global``/``unsubscribe_global``/``publish``/
``format_sse``) stays the same so call sites in services and
the workshops router do not change.
"""

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_workshop_subscribers: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
_global_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_lock = asyncio.Lock()


async def subscribe(workshop_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
    """Register a new subscriber for a single workshop's events.

    Currently unused by the public API but kept for future per-
    workshop SSE channels. If you wire one up, the queue returned
    here will receive events whose ``workshop_id`` matches
    ``workshop_id``.

    Args:
        workshop_id: Workshop whose events this subscriber wants.

    Returns:
        An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
    async with _lock:
        _workshop_subscribers[workshop_id].add(queue)
    return queue


async def subscribe_global() -> asyncio.Queue[dict[str, Any]]:
    """Register a subscriber that receives events for every workshop.

    Used by the ``GET /api/workshops/events`` SSE endpoint so the
    browser can open a single stream and filter by ``workshop_id``
    on each payload.

    Returns:
        An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    async with _lock:
        _global_subscribers.add(queue)
    return queue


async def unsubscribe(workshop_id: uuid.UUID, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove a workshop-scoped subscriber.

    Args:
        workshop_id: Workshop the queue was registered against.
        queue: The queue previously returned by :func:`subscribe`.
    """
    async with _lock:
        subscribers = _workshop_subscribers.get(workshop_id)
        if subscribers and queue in subscribers:
            subscribers.discard(queue)
            if not subscribers:
                _workshop_subscribers.pop(workshop_id, None)


async def unsubscribe_global(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove a global subscriber.

    Args:
        queue: The queue previously returned by :func:`subscribe_global`.
    """
    async with _lock:
        _global_subscribers.discard(queue)


async def publish(workshop_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Broadcast an event to workshop-scoped and global subscribers.

    Slow consumers (full queues) are dropped rather than blocking
    the publisher. The frontend will catch up on the next interaction
    (e.g. when the user opens a workshop detail view).

    Args:
        workshop_id: Target workshop. Used for the workshop-scoped
            filter and embedded in the payload for the global stream.
        event: JSON-serializable payload to deliver.
    """
    async with _lock:
        targets: list[asyncio.Queue[dict[str, Any]]] = list(
            _workshop_subscribers.get(workshop_id, ())
        )
        targets.extend(_global_subscribers)
    for queue in targets:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.debug("realtime.queue_full_dropped", workshop_id=str(workshop_id))


def format_sse(event: dict[str, Any]) -> bytes:
    """Serialize an event dict into a single SSE message.

    Args:
        event: Payload to serialize. ``datetime`` instances are
            converted via ``default=str`` so naive ISO formatting is
            acceptable.

    Returns:
        Bytes ready to write to the response stream. Format:
        ``data: {json}\\n\\n``.
    """
    return f"data: {json.dumps(event, default=str)}\n\n".encode()
