"""Redis realtime bus adapter tests.

These tests exercise cross-replica delivery: two
buses sharing one Redis instance, where a publish
on one reaches subscribers on the other. They are
skipped when no Redis is reachable at
``REDIS_URL`` (the default ``redis://localhost:6379``),
so the suite stays green on machines without Redis.
"""

import asyncio
import uuid

import pytest
import redis
from ws_core.realtime.redis import RedisRealtimeBus

REDIS_URL = "redis://localhost:6379/0"


def _redis_reachable() -> bool:
    """Return whether a Redis server answers at ``REDIS_URL``."""
    try:
        return bool(redis.Redis.from_url(REDIS_URL).ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_reachable(),
    reason=f"no Redis reachable at {REDIS_URL}",
)


@pytest.mark.asyncio
async def test_publish_delivers_to_other_bus() -> None:
    """A publish on one replica reaches the other replica's subscribers.

    Bus B's listener is running, so the message bus A publishes
    to the Redis channel is delivered to B's global subscriber.
    """
    bus_a = RedisRealtimeBus(REDIS_URL)
    bus_b = RedisRealtimeBus(REDIS_URL)
    await bus_b.start()
    try:
        queue = await bus_b.subscribe_global()
        workshop_id = uuid.uuid4()
        await bus_a.publish(
            workshop_id,
            {"workshop_id": str(workshop_id), "type": "x"},
        )
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["workshop_id"] == str(workshop_id)
        assert event["type"] == "x"
    finally:
        await bus_b.stop()
        await bus_a.stop()


@pytest.mark.asyncio
async def test_own_publish_delivers_exactly_once() -> None:
    """The publishing replica ignores its own Redis echo.

    Local delivery happens at publish time; the message the
    listener then receives carries this replica's
    ``publisher_id`` and is filtered, so the subscriber sees
    the event exactly once.
    """
    bus = RedisRealtimeBus(REDIS_URL)
    await bus.start()
    try:
        queue = await bus.subscribe_global()
        workshop_id = uuid.uuid4()
        await bus.publish(
            workshop_id,
            {"workshop_id": str(workshop_id), "type": "y"},
        )
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["type"] == "y"
        # A second delivery would mean the own-message
        # filter failed.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.5)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_publisher_ids_differ_per_instance() -> None:
    """Each bus instance owns a distinct publisher id."""
    bus_a = RedisRealtimeBus(REDIS_URL)
    bus_b = RedisRealtimeBus(REDIS_URL)
    try:
        assert bus_a.publisher_id != bus_b.publisher_id
    finally:
        await bus_a.stop()
        await bus_b.stop()
