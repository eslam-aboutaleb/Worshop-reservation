"""ws-reservation: pluggable reservation domain plugin.

This package owns the reservation domain: workshops,
reservations, the waitlist, organizations and memberships,
follows, reviews, idempotency keys, and the domain events
those mutations emit. It mounts into any FastAPI application
via ``ReservationPlugin().register(app, container)`` and
depends only on ``ws-core`` for its cross-cutting
infrastructure.

The plugin is self-contained: it ships its own models,
schemas, services, routers, migrations, and the SSE projector
that translates domain events into realtime broadcasts. The
application becomes a thin composition root that supplies the
``Container`` (settings, engine, session factory, event bus,
realtime bus, rate limiter).

Usage
-------

    from ws_core.app import create_app
    from ws_reservation import ReservationPlugin

    app = create_app(settings, plugins=[ReservationPlugin()])
"""

from ws_reservation.plugin import ReservationPlugin
from ws_reservation.routers import create_router

__all__ = ["ReservationPlugin", "create_router"]
