"""Project domain events onto the realtime bus.

This is the single consumer of the plugin's event bus:
it translates every domain event into the realtime
broadcast the frontend's ``EventSource`` already
consumes. Because the event payloads are byte-identical
to the pre-event-bus SSE broadcasts, the projector is a
pure mapping - ``realtime.publish(workshop_id,
event.payload)``.

The projector is registered by the plugin's
``register()`` with the container's event bus. The
module tracks which buses it has subscribed to, so
registering the same bus twice (a second
``create_app()`` that reuses a bus, or a repeated
plugin registration) cannot double-subscribe and
deliver every event twice.
"""

import uuid

from ws_core.events import DomainEvent, EventBus
from ws_core.realtime import publish

_subscribed_buses: set[EventBus] = set()


def register_realtime_projector(event_bus: EventBus) -> None:
    """Subscribe the SSE projector to ``event_bus``.

    Idempotent per bus: the first call for a given bus
    subscribes; later calls for the same bus are no-ops.

    Args:
        event_bus: The domain event bus to consume.
    """
    if event_bus in _subscribed_buses:
        return
    _subscribed_buses.add(event_bus)
    event_bus.subscribe(_project)


async def _project(event: DomainEvent) -> None:
    """Broadcast ``event`` on the realtime bus.

    Every domain event carries its workshop id in the
    payload, so the mapping needs no per-type branching.

    Args:
        event: The domain event to broadcast.
    """
    workshop_id = uuid.UUID(str(event.payload["workshop_id"]))
    await publish(workshop_id, event.payload)
