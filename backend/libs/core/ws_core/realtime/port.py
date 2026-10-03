"""Realtime bus port.

Mirrors the surface the SSE endpoint and the services
have relied on since the first realtime plan: publish an
event for a workshop, and register global or
workshop-scoped subscriber queues. ``format_sse`` stays a
pure module function (``ws_core.realtime``) because it
serializes a payload, it does not touch the bus.

Implementations:

* :class:`~ws_core.realtime.in_process.InProcessRealtimeBus`
  - single replica, ``asyncio.Queue`` subscribers.
* :class:`~ws_core.realtime.redis.RedisRealtimeBus`
  - multi-replica, Redis pub/sub with local delivery.

The composition root selects one via
:func:`~ws_core.realtime.factory.create_realtime_bus`
and installs it with
:func:`~ws_core.realtime.factory.set_realtime_bus`.
"""

import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import Any

EventQueue = asyncio.Queue[dict[str, Any]]
"""Subscriber queue: each queued item is one SSE payload."""


class RealtimeBus(ABC):
    """Port for workshop-event delivery to connected browsers."""

    @abstractmethod
    async def publish(self, workshop_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Deliver ``event`` to every subscriber of ``workshop_id``.

        Slow consumers (full queues) are dropped rather than
        blocking the publisher.

        Args:
            workshop_id: Workshop the event belongs to.
            event: JSON-serializable SSE payload.
        """

    @abstractmethod
    async def subscribe_global(self) -> EventQueue:
        """Register a queue that receives every workshop's events.

        Returns:
            The queue to ``get()`` events from.
        """

    @abstractmethod
    async def subscribe(self, workshop_id: uuid.UUID) -> EventQueue:
        """Register a queue that receives one workshop's events.

        Args:
            workshop_id: Workshop to observe.

        Returns:
            The queue to ``get()`` events from.
        """

    @abstractmethod
    async def unsubscribe_global(self, queue: EventQueue) -> None:
        """Remove a global subscriber.

        Args:
            queue: The queue previously returned by
                :meth:`subscribe_global`.
        """

    @abstractmethod
    async def unsubscribe(self, workshop_id: uuid.UUID, queue: EventQueue) -> None:
        """Remove a workshop-scoped subscriber.

        Args:
            workshop_id: Workshop the queue was registered against.
            queue: The queue previously returned by
                :meth:`subscribe`.
        """

    async def start(self) -> None:  # noqa: B027 - intentional no-op default
        """Start background delivery infrastructure.

        No-op for in-process buses; the Redis adapter uses
        it to spawn its pub/sub listener task. The
        composition root calls this on app startup.
        """

    async def stop(self) -> None:  # noqa: B027 - intentional no-op default
        """Stop background delivery infrastructure.

        No-op for in-process buses; the Redis adapter uses
        it to cancel its listener and close the connection.
        The composition root calls this on app shutdown.
        """
