"""Realtime pub/sub for Server-Sent Events.

This module is the **facade** over the realtime bus
port (:mod:`ws_core.realtime.port`). The module-level
functions delegate to the process-wide default bus
(:func:`~ws_core.realtime.factory.get_realtime_bus`),
so existing call sites - the SSE router, the event
projector, the test suite - keep importing
``ws_core.realtime`` and never touch a concrete
implementation.

The default bus is selected by configuration:
an empty ``redis_url`` (the default) uses the
in-process bus; a URL uses the Redis adapter, which
delivers events across replicas (see
:mod:`ws_core.realtime.redis` for the exactly-once
per-replica delivery design).

The single-channel design lets the frontend open one
``EventSource`` and filter by ``workshop_id`` on each
event payload.
"""

import json
import uuid
from typing import Any

from ws_core.realtime.factory import (
    create_realtime_bus,
    get_realtime_bus,
    set_realtime_bus,
)
from ws_core.realtime.in_process import InProcessRealtimeBus
from ws_core.realtime.port import EventQueue, RealtimeBus
from ws_core.realtime.redis import RedisRealtimeBus

__all__ = [
    "EventQueue",
    "InProcessRealtimeBus",
    "RealtimeBus",
    "RedisRealtimeBus",
    "create_realtime_bus",
    "format_sse",
    "get_realtime_bus",
    "publish",
    "set_realtime_bus",
    "subscribe",
    "subscribe_global",
    "unsubscribe",
    "unsubscribe_global",
]


async def publish(workshop_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Broadcast an event via the default bus.

    Args:
        workshop_id: Target workshop.
        event: JSON-serializable payload to deliver.
    """
    await get_realtime_bus().publish(workshop_id, event)


async def subscribe(workshop_id: uuid.UUID) -> EventQueue:
    """Register a queue for one workshop's events.

    Args:
        workshop_id: Workshop whose events this subscriber wants.

    Returns:
        An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
    """
    return await get_realtime_bus().subscribe(workshop_id)


async def subscribe_global() -> EventQueue:
    """Register a queue that receives events for every workshop.

    Used by the ``GET /api/workshops/events`` SSE endpoint so the
    browser can open a single stream and filter by ``workshop_id``
    on each payload.

    Returns:
        An ``asyncio.Queue`` the caller can ``get()`` from in a loop.
    """
    return await get_realtime_bus().subscribe_global()


async def unsubscribe(workshop_id: uuid.UUID, queue: EventQueue) -> None:
    """Remove a workshop-scoped subscriber.

    Args:
        workshop_id: Workshop the queue was registered against.
        queue: The queue previously returned by :func:`subscribe`.
    """
    await get_realtime_bus().unsubscribe(workshop_id, queue)


async def unsubscribe_global(queue: EventQueue) -> None:
    """Remove a global subscriber.

    Args:
        queue: The queue previously returned by :func:`subscribe_global`.
    """
    await get_realtime_bus().unsubscribe_global(queue)


def format_sse(event: dict[str, Any]) -> bytes:
    """Serialize an event dict into a single SSE message.

    Pure function: it touches no bus state, so it stays
    at module level rather than on the port.

    Args:
        event: Payload to serialize. ``datetime`` instances are
            converted via ``default=str`` so naive ISO formatting is
            acceptable.

    Returns:
        Bytes ready to write to the response stream. Format:
        ``data: {json}\\n\\n``.
    """
    return f"data: {json.dumps(event, default=str)}\n\n".encode()
