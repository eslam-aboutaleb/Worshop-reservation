"""FastAPI application entry point.

Responsibilities of this module
--------------------------------

* Build the ``FastAPI`` app instance.
* Wire CORS from ``Settings.cors_origins`` (a JSON-decoded list of
  origins, see ``configuration/settings.py``).
* Register exception handlers for every custom domain exception
  declared in ``src/exceptions.py``. The handlers produce a uniform
  ``{"error": {"code": "...", "message": "..."}}`` envelope so the
  frontend can branch on ``error.code``.
* Mount the versioned HTTP API under ``/api`` (the router itself
  declares its own ``/workshops``, ``/reservations``, ``/auth``
  prefixes - see ``src/api/routers/__init__.py``).
* Expose ``/health`` at the root so Docker Compose's healthcheck
  (``docker-compose.yml``) can probe the container without going
  through the versioned API.

The module is also the CLI entry point: running ``python -m src.main``
starts Uvicorn against the configured host/port. In the Docker image
the same command is invoked by the shell wrapper in ``Dockerfile``
after Alembic has run and the demo data has been seeded.
"""

import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import router as api_router
from src.configuration.database import async_session_factory
from src.configuration.logging import configure_logging
from src.configuration.settings import get_settings
from src.exceptions import (
    AlreadyReservedError,
    CannotFollowOwnOrganizationError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    OrganizationNotFoundError,
    RateLimitedError,
    RegistrationClosedError,
    ReservationNotFoundError,
    ReviewAlreadyExistsError,
    ReviewNotEligibleError,
    WaitlistEntryNotFoundError,
    WorkshopFullError,
    WorkshopHasActiveReservationsError,
    WorkshopNotFoundError,
    domain_error_handler,
)
from src.models.idempotency_key import IdempotencyKey

settings = get_settings()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Kept as a factory (rather than a top-level singleton) so tests can
    instantiate an isolated app instance and ``dependency_overrides``
    don't leak across tests.

    Returns:
        A fully wired ``FastAPI`` instance ready to be served.
    """
    configure_logging()

    app = FastAPI(
        title="Workshop Reservations API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start_time = time.perf_counter()
        logger = structlog.stdlib.get_logger("api.access")

        try:
            response = await call_next(request)
            process_time = time.perf_counter() - start_time
            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round(process_time * 1000, 2),
            )
            return response
        except Exception:
            process_time = time.perf_counter() - start_time
            logger.exception(
                "request_failed",
                duration_ms=round(process_time * 1000, 2),
            )
            raise

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        max_age=600,
    )

    app.add_exception_handler(WorkshopNotFoundError, domain_error_handler)
    app.add_exception_handler(ReservationNotFoundError, domain_error_handler)
    app.add_exception_handler(WorkshopFullError, domain_error_handler)
    app.add_exception_handler(AlreadyReservedError, domain_error_handler)
    app.add_exception_handler(WorkshopHasActiveReservationsError, domain_error_handler)
    app.add_exception_handler(RegistrationClosedError, domain_error_handler)
    app.add_exception_handler(RateLimitedError, domain_error_handler)
    app.add_exception_handler(WaitlistEntryNotFoundError, domain_error_handler)
    app.add_exception_handler(EmailAlreadyExistsError, domain_error_handler)
    app.add_exception_handler(InvalidCredentialsError, domain_error_handler)
    app.add_exception_handler(OrganizationNotFoundError, domain_error_handler)
    app.add_exception_handler(CannotFollowOwnOrganizationError, domain_error_handler)
    app.add_exception_handler(ReviewNotEligibleError, domain_error_handler)
    app.add_exception_handler(ReviewAlreadyExistsError, domain_error_handler)

    app.include_router(api_router, prefix="/api")

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
        from datetime import UTC, datetime

        from sqlalchemy import delete as sa_delete

        try:
            async with async_session_factory() as session:
                await session.execute(
                    sa_delete(IdempotencyKey).where(
                        IdempotencyKey.expires_at < datetime.now(UTC)
                    )
                )
                await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception("idempotency_sweep_failed")

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        """Liveness probe used by ``docker-compose.yml`` and orchestrators.

        Returns:
            A constant ``{"status": "healthy"}`` payload with HTTP 200.
            This endpoint intentionally does not check the database -
            a slow query would flap the container. Add a separate
            ``/readiness`` probe if you need a dependency check.
        """
        return {"status": "healthy"}

    return app


# Module-level instance for Uvicorn (`uvicorn src.main:app`).
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_config=None,
    )
