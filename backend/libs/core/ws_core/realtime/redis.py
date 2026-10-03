"""Redis pub/sub realtime bus (multi-replica).

A single in-process bus cannot reach browsers
connected to a *different* replica. This adapter
wraps an :class:`~ws_core.realtime.in_process.InProcessRealtimeBus`
for local delivery and adds a Redis pub/sub leg:

* :meth:`publish` delivers to local subscriber
  queues **immediately** (local clients never wait
  on the Redis round-trip or on the listener
  running) and then publishes the payload to the
  ``workshop_events`` channel.
* A background listener (:meth:`start`) receives
  channel messages and delivers them locally,
  **skipping its own ``publisher_id``**. The result
  is exactly one delivery per replica: the
  publishing replica delivers locally at publish
  time and ignores its own Redis echo; every other
  replica delivers via its listener.

Message envelope (JSON)::

    {"publisher_id": "<uuid>", "workshop_id": "<uuid>", "event": {<SSE payload>}}

Payloads are tiny (workshop id, event type, spot
counts), so message size and backpressure are not a
concern.
"""

import asyncio
import contextlib
import json
import uuid
from typing import Any

import redis.asyncio as redis
import structlog

from ws_core.realtime.in_process import InProcessRealtimeBus
from ws_core.realtime.port import EventQueue, RealtimeBus

logger = structlog.get_logger(__name__)

CHANNEL = "workshop_events"
"""Redis pub/sub channel carrying workshop events."""


class RedisRealtimeBus(RealtimeBus):
    """Multi-replica bus backed by Redis pub/sub."""

    def __init__(self, redis_url: str) -> None:
        self._publisher_id = str(uuid.uuid4())
        self._redis = redis.from_url(redis_url)
        self._local = InProcessRealtimeBus()
        self._listener: asyncio.Task[None] | None = None

    @property
    def publisher_id(self) -> str:
        """The id this replica filters its own messages by."""
        return self._publisher_id

    async def publish(self, workshop_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Deliver locally, then broadcast to other replicas.

        Args:
            workshop_id: Target workshop.
            event: JSON-serializable SSE payload.
        """
        # Local first: connected clients on this replica
        # get the event even if the listener is not
        # running (or Redis is briefly unavailable - the
        # publish below would raise, which the caller's
        # error handling owns).
        await self._local.publish(workshop_id, event)
        message = json.dumps(
            {
                "publisher_id": self._publisher_id,
                "workshop_id": str(workshop_id),
                "event": event,
            }
        )
        await self._redis.publish(CHANNEL, message)

    async def subscribe_global(self) -> EventQueue:
        """Register a queue receiving every workshop's events.

        Args:
            None.

        Returns:
            The local queue to ``get()`` events from.
        """
        return await self._local.subscribe_global()

    async def subscribe(self, workshop_id: uuid.UUID) -> EventQueue:
        """Register a queue receiving one workshop's events.

        Args:
            workshop_id: Workshop to observe.

        Returns:
            The local queue to ``get()`` events from.
        """
        return await self._local.subscribe(workshop_id)

    async def unsubscribe_global(self, queue: EventQueue) -> None:
        """Remove a global subscriber.

        Args:
            queue: The queue previously returned by
                :meth:`subscribe_global`.
        """
        await self._local.unsubscribe_global(queue)

    async def unsubscribe(self, workshop_id: uuid.UUID, queue: EventQueue) -> None:
        """Remove a workshop-scoped subscriber.

        Args:
            workshop_id: Workshop the queue was registered against.
            queue: The queue previously returned by :meth:`subscribe`.
        """
        await self._local.unsubscribe(workshop_id, queue)

    async def start(self) -> None:
        """Spawn the pub/sub listener task.

        Idempotent: a running listener is left alone.
        """
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._listen())
            logger.info(
                "realtime.redis_listener_started",
                publisher_id=self._publisher_id,
            )

    async def stop(self) -> None:
        """Cancel the listener and close the connection.

        Safe to call more than once.
        """
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        await self._redis.aclose()

    async def _listen(self) -> None:
        """Deliver cross-replica messages to local subscribers.

        Runs until :meth:`stop` cancels it. Messages from
        this replica (matched on ``publisher_id``) are
        skipped - their local delivery already happened
        at publish time.
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    if data["publisher_id"] == self._publisher_id:
                        continue
                    await self._local.publish(
                        uuid.UUID(data["workshop_id"]),
                        data["event"],
                    )
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    # A malformed cross-replica message must not
                    # kill the listener: every other replica's
                    # events would stop being delivered. Log and
                    # keep consuming.
                    logger.warning(
                        "realtime.redis_malformed_message",
                        error="malformed pub/sub payload",
                    )
        except asyncio.CancelledError:
            await pubsub.unsubscribe()
            raise
