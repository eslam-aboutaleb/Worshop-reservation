"""In-process realtime bus (single replica).

Each connected SSE client owns an ``asyncio.Queue``. The
event projector calls :meth:`publish` on every domain
event; the queue gets a copy of the event payload; the
SSE generator yields it as a ``data: ...\\n\\n`` message.

Why two subscriber sets?
------------------------

* ``_global_subscribers`` receives every workshop's
  events. This is what the public SSE endpoint
  (``GET /api/workshops/events``) uses so the browser can
  render live spot counts on the list page.
* ``_workshop_subscribers`` is reserved for per-workshop
  channels. No per-workshop endpoint is exposed today,
  but the data structure is in place if/when one is added.

Concurrency
-----------

All subscriber-set mutations happen under ``_lock``; the
``publish`` snapshot is taken under the lock and then
iterated without it so a slow subscriber cannot stall the
producer. ``put_nowait`` on a full queue is dropped
(logged at debug level) so a stuck client cannot back up
the event loop; the next user action will re-fetch the
canonical state via the workshop detail endpoint.

This is the exact implementation that lived at module
scope before the port extraction; the state simply moved
onto the instance so the process can hold more than one
bus (tests, and the Redis adapter's local-delivery leg).
"""

import asyncio
import uuid
from collections import defaultdict
from typing import Any

import structlog

from ws_core.realtime.port import EventQueue, RealtimeBus

logger = structlog.get_logger(__name__)


class InProcessRealtimeBus(RealtimeBus):
    """Single-replica bus with ``asyncio.Queue`` subscribers."""

    def __init__(self) -> None:
        self._workshop_subscribers: dict[uuid.UUID, set[EventQueue]] = defaultdict(set)
        self._global_subscribers: set[EventQueue] = set()
        # A threading.Lock would also work (the critical
        # sections are synchronous), but every access here
        # happens on the event loop, so an asyncio.Lock is
        # the idiomatic choice. Python 3.10+ locks do not
        # bind to a loop until awaited, so the per-test
        # event loops pytest-asyncio creates are safe.
        self._lock = asyncio.Lock()

    async def subscribe(self, workshop_id: uuid.UUID) -> EventQueue:
        """Register a new subscriber for a single workshop's events.

        Currently unused by the public API but kept for future
        per-workshop SSE channels. If you wire one up, the queue
        returned here will receive events whose ``workshop_id``
        matches ``workshop_id``.

        Args:
            workshop_id: Workshop whose events this subscriber wants.

        Returns:
            An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
        """
        queue: EventQueue = asyncio.Queue(maxsize=128)
        async with self._lock:
            self._workshop_subscribers[workshop_id].add(queue)
        return queue

    async def subscribe_global(self) -> EventQueue:
        """Register a subscriber that receives events for every workshop.

        Used by the ``GET /api/workshops/events`` SSE endpoint so the
        browser can open a single stream and filter by ``workshop_id``
        on each payload.

        Returns:
            An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
        """
        queue: EventQueue = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._global_subscribers.add(queue)
        return queue

    async def unsubscribe(self, workshop_id: uuid.UUID, queue: EventQueue) -> None:
        """Remove a workshop-scoped subscriber.

        Args:
            workshop_id: Workshop the queue was registered against.
            queue: The queue previously returned by :meth:`subscribe`.
        """
        async with self._lock:
            subscribers = self._workshop_subscribers.get(workshop_id)
            if subscribers and queue in subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._workshop_subscribers.pop(workshop_id, None)

    async def unsubscribe_global(self, queue: EventQueue) -> None:
        """Remove a global subscriber.

        Args:
            queue: The queue previously returned by :meth:`subscribe_global`.
        """
        async with self._lock:
            self._global_subscribers.discard(queue)

    async def publish(self, workshop_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Broadcast an event to workshop-scoped and global subscribers.

        Slow consumers (full queues) are dropped rather than blocking
        the publisher. The frontend will catch up on the next
        interaction (e.g. when the user opens a workshop detail view).

        Args:
            workshop_id: Target workshop. Used for the workshop-scoped
                filter and embedded in the payload for the global stream.
            event: JSON-serializable payload to deliver.
        """
        async with self._lock:
            targets: list[EventQueue] = list(self._workshop_subscribers.get(workshop_id, ()))
            targets.extend(self._global_subscribers)
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "realtime.queue_full_dropped",
                    workshop_id=str(workshop_id),
                )
