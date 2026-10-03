"""The reservation domain plugin.

:class:`ReservationPlugin` mounts the entire
reservation domain - workshops, reservations, waitlist,
organizations, reviews - onto any FastAPI app composed
with :func:`ws_core.app.create_app`:

    app = create_app(settings, plugins=[ReservationPlugin()])

What ``register`` wires up
---------------------------

* The domain routers under ``/api`` (bound to the
  container, so services publish on the container's
  event bus).
* Exception handlers for every domain error the
  services raise, producing the uniform
  ``{"error": {"code", "message"}}`` envelope.
* The SSE projector: domain events are translated
  onto the realtime bus (idempotent per event bus).
* A startup sweep that deletes expired
  idempotency-key rows (the plugin owns that table).
"""

from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from sqlalchemy import delete as sa_delete

from ws_core.container import Container
from ws_core.db import engine as db_engine
from ws_core.errors import (
    AlreadyReservedError,
    CannotFollowOwnOrganizationError,
    OrganizationNotFoundError,
    RegistrationClosedError,
    ReservationNotFoundError,
    ReviewAlreadyExistsError,
    ReviewNotEligibleError,
    WaitlistEntryNotFoundError,
    WorkshopFullError,
    WorkshopHasActiveReservationsError,
    WorkshopNotFoundError,
    register_domain_handlers,
)
from ws_core.app import Plugin

from ws_reservation.models.idempotency_key import IdempotencyKey
from ws_reservation.routers import create_router
from ws_reservation.sse_projector import register_realtime_projector

logger = structlog.get_logger(__name__)

# Every domain error the plugin's services can raise. Each is
# bound to the shared domain_error_handler, which renders the
# uniform {"error": {"code", "message"}} envelope. (The core
# registers the errors its own /auth router raises.)
_DOMAIN_ERRORS = [
    WorkshopNotFoundError,
    ReservationNotFoundError,
    WorkshopFullError,
    AlreadyReservedError,
    WorkshopHasActiveReservationsError,
    RegistrationClosedError,
    WaitlistEntryNotFoundError,
    OrganizationNotFoundError,
    CannotFollowOwnOrganizationError,
    ReviewNotEligibleError,
    ReviewAlreadyExistsError,
]


class ReservationPlugin:
    """Pluggable reservation domain for a ws-core composition root."""

    def register(self, app: FastAPI, container: Container) -> None:
        """Mount the reservation domain on ``app``.

        Args:
            app: The FastAPI application being composed.
            container: The dependencies supplied by the
                composition root; the domain routers bind to
                it and the SSE projector consumes its event
                bus.
        """
        # Domain routes under /api (the core /auth router is
        # mounted by the composition root).
        app.include_router(create_router(container), prefix="/api")

        # Translate every domain error into the shared error
        # envelope.
        register_domain_handlers(app, _DOMAIN_ERRORS)

        # Translate domain events onto the realtime bus.
        # Idempotent per event bus, so repeated
        # registrations cannot double-subscribe.
        register_realtime_projector(container.event_bus)

        @app.on_event("startup")
        async def sweep_expired_idempotency_keys() -> None:
            """Delete expired idempotency-key rows once at boot.

            The ``idempotency_keys`` table grows by one row per
            successful reservation. Without a sweep it is unbounded
            (plan 0.4). Replay already filters on ``expires_at``,
            so expired rows are dead weight; this keeps the table
            bounded. Runs after Alembic (the Dockerfile applies
            migrations before uvicorn starts) and is best-effort: a
            failure here must never block startup.
            """
            try:
                async with db_engine.async_session_factory() as session:
                    await session.execute(
                        sa_delete(IdempotencyKey).where(
                            IdempotencyKey.expires_at < datetime.now(UTC)
                        )
                    )
                    await session.commit()
            except Exception:  # pragma: no cover - defensive
                logger.exception("idempotency_sweep_failed")
