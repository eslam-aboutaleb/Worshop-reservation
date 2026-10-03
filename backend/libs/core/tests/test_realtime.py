"""Tests for the realtime bus.

The suite covers three layers:

* the pure SSE serializer (``format_sse``),
* the module-level facade (delegation to the
  process-wide default bus), and
* the in-process bus internals (subscriber
  registration, unsubscribe idempotency, and the
  slow-consumer drop behavior).

The Redis adapter has its own suite
(``test_realtime_redis.py``) that skips when no
Redis is reachable.
"""

import asyncio
import json
import uuid

import pytest
from ws_core.realtime import (
    format_sse,
    publish,
    subscribe,
    subscribe_global,
    unsubscribe,
    unsubscribe_global,
)
from ws_core.realtime.in_process import InProcessRealtimeBus


def test_format_sse_serializes_dict_to_data_line() -> None:
    """The output is a single 'data: <json>\\n\\n' message."""
    payload = {"type": "reservation_created", "workshop_id": "abc"}
    message = format_sse(payload)
    assert message.endswith(b"\n\n")
    line, _, _ = message.partition(b"\n")
    assert line.startswith(b"data: ")
    body = json.loads(line[len(b"data: ") :].decode())
    assert body == payload


def test_format_sse_handles_datetime_via_default_str() -> None:
    """datetime values are serialized via str() so the encoder never raises."""
    from datetime import UTC, datetime

    payload = {"when": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)}
    message = format_sse(payload)
    line = message.split(b"\n", 1)[0]
    body = json.loads(line[len(b"data: ") :].decode())
    assert "2026" in body["when"]


@pytest.mark.asyncio
async def test_publish_delivers_to_global_subscriber() -> None:
    """An event published to a workshop reaches every global subscriber."""
    queue = await subscribe_global()
    try:
        workshop_id = uuid.uuid4()
        await publish(workshop_id, {"workshop_id": str(workshop_id), "type": "x"})
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["workshop_id"] == str(workshop_id)
    finally:
        await unsubscribe_global(queue)


@pytest.mark.asyncio
async def test_publish_delivers_to_workshop_scoped_subscriber_only() -> None:
    """A workshop-scoped queue receives events for its workshop and nothing else."""
    target = uuid.uuid4()
    other = uuid.uuid4()
    queue = await subscribe(target)
    try:
        await publish(target, {"workshop_id": str(target), "type": "x"})
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["workshop_id"] == str(target)

        # Publishing for a different workshop must not deliver.
        await publish(other, {"workshop_id": str(other), "type": "y"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.2)
    finally:
        await unsubscribe(target, queue)


@pytest.mark.asyncio
async def test_unsubscribe_global_is_idempotent() -> None:
    """Unsubscribe on an unknown queue is a no-op (no exception)."""
    bus = InProcessRealtimeBus()
    queue = await bus.subscribe_global()
    await bus.unsubscribe_global(queue)
    await bus.unsubscribe_global(queue)  # second call must not raise
    assert queue not in bus._global_subscribers


@pytest.mark.asyncio
async def test_slow_consumer_is_dropped_not_blocked() -> None:
    """A full queue causes the publish to drop the event, not block."""
    bus = InProcessRealtimeBus()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"existing": True})
    # Hand-register the saturated queue so the publisher sees it.
    async with bus._lock:
        bus._global_subscribers.add(queue)
    try:
        # Publishing should not block even though the queue is full.
        await asyncio.wait_for(bus.publish(uuid.uuid4(), {"type": "x"}), timeout=0.5)
        # The original event is still there; the new one was dropped.
        assert await queue.get() == {"existing": True}
    finally:
        async with bus._lock:
            bus._global_subscribers.discard(queue)
