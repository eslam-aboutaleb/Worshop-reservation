"""Project domain events onto the realtime bus.

This is the single consumer of the app event bus
(``src.events``): it translates every domain event
into the realtime broadcast the frontend's
``EventSource`` already consumes. Because the event
payloads are byte-identical to the pre-event-bus
SSE broadcasts, the projector is a pure mapping -
``realtime.publish(workshop_id, event.payload)``.

The projector is registered once per process: the
module-level flag makes :func:`register_realtime_projector`
idempotent, so repeated ``create_app()`` calls (the
test suite builds the app at import time and again
in isolated cases) cannot double-subscribe and
deliver every event twice.
"""

import uuid

from ws_core.events import DomainEvent
from ws_core.realtime import publish

from src.events import get_event_bus

_projector_registered = False


def register_realtime_projector() -> None:
    """Subscribe the SSE projector to the app event bus.

    Idempotent: the first call subscribes; later calls
    are no-ops.
    """
    global _projector_registered
    if _projector_registered:
        return
    _projector_registered = True
    get_event_bus().subscribe(_project)


async def _project(event: DomainEvent) -> None:
    """Broadcast ``event`` on the realtime bus.

    Every domain event carries its workshop id in the
    payload, so the mapping needs no per-type branching.

    Args:
        event: The domain event to broadcast.
    """
    workshop_id = uuid.UUID(str(event.payload["workshop_id"]))
    await publish(workshop_id, event.payload)
