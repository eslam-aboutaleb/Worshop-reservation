"""Generic FastAPI composition root.

:func:`create_app` builds the process-wide infrastructure
(structlog configuration, the async database engine, the
realtime bus), assembles a :class:`~ws_core.container.Container`,
and mounts every plugin's routes on a fresh ``FastAPI``
instance. The application keeps only its own settings and
the plugin list; all domain behavior travels with the
plugins (see ``ws-reservation``).

What the root owns
-------------------

* The logging middleware (request id, method, path,
  duration) and CORS, both configured from settings.
* The core ``/auth`` router (signup, login, ``/me``,
  logout), mounted under ``/api``.
* The ``/health`` liveness probe.
* The realtime bus lifecycle: the bus selected by
  ``settings.redis_url`` is installed as the process-wide
  default and started/stopped with the app.
* Exception handlers for the errors the core itself
  raises (auth rate limiting, duplicate signup, bad
  credentials). Domain errors are registered by the
  plugin that raises them.

The module is also the CLI entry point pattern: the
application's ``src.main`` calls :func:`create_app` once
at import time so ``uvicorn src.main:app`` works.
"""

import time
import uuid
from collections.abc import Iterable
from typing import Protocol

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from ws_core.auth.routers import router as auth_router
from ws_core.config import CoreSettings
from ws_core.container import Container, DefaultContainer
from ws_core.db.engine import async_session_factory, init_db
from ws_core.errors import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    RateLimitedError,
    register_domain_handlers,
)
from ws_core.events.in_process import InProcessEventBus
from ws_core.logging import configure_logging
from ws_core.rate_limit import get_rate_limiter
from ws_core.realtime.factory import (
    create_realtime_bus,
    get_realtime_bus,
    set_realtime_bus,
)

logger = structlog.get_logger(__name__)


class Plugin(Protocol):
    """A domain plugin mounted by :func:`create_app`.

    A plugin registers its routes, exception handlers and
    startup hooks on the app, reading its dependencies
    from the container.
    """

    def register(self, app: FastAPI, container: Container) -> None:
        """Mount the plugin on ``app``.

        Args:
            app: The FastAPI application being composed.
            container: The dependencies the plugin needs.
        """


# Every error the core itself can raise. Each is bound to
# the shared domain_error_handler, which renders the
# uniform {"error": {"code", "message"}} envelope.
_CORE_ERRORS = [
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    RateLimitedError,
]


class _AccessLogMiddleware:
    """Pure ASGI access-log middleware.

    Wraps the downstream app directly (awaited) instead
    of going through ``BaseHTTPMiddleware``, whose
    ``call_next`` runs the request handler inside a
    separate anyio task. Avoiding that task both cuts
    per-request overhead and keeps the handler on the
    caller's coroutine stack, which also keeps the
    request path observable to coverage tracers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=headers.get("x-request-id", str(uuid.uuid4())),
            method=scope["method"],
            path=scope["path"],
        )

        start_time = time.perf_counter()
        access_logger = structlog.stdlib.get_logger("api.access")
        status_code = {"value": 500}

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_code["value"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
            access_logger.info(
                "request_completed",
                status_code=status_code["value"],
                duration_ms=round(
                    (time.perf_counter() - start_time) * 1000, 2
                ),
            )
        except Exception:
            access_logger.exception(
                "request_failed",
                duration_ms=round(
                    (time.perf_counter() - start_time) * 1000, 2
                ),
            )
            raise


def create_app(
    settings: CoreSettings,
    plugins: Iterable[Plugin] = (),
    *,
    title: str = "Workshop Reservations API",
    version: str = "1.0.0",
) -> FastAPI:
    """Build and configure the FastAPI application.

    Kept as a factory (rather than a top-level singleton) so
    tests can instantiate an isolated app instance and
    ``dependency_overrides`` don't leak across tests.

    Args:
        settings: The resolved settings. They are registered
            with ws-core, the engine and realtime bus are
            built from them, and they are carried on the
            container for the plugins.
        plugins: Domain plugins to mount. Each plugin's
            ``register`` is called with the app and the
            container, in iteration order.
        title: OpenAPI document title.
        version: OpenAPI document version.

    Returns:
        A fully wired ``FastAPI`` instance ready to be served.
    """
    configure_logging()
    # Build the process-wide async engine and session factory from
    # the resolved settings. Done here (not at import time) so the
    # engine is configured exactly once, after settings are
    # registered with ws-core, and so re-configuration never leaks
    # a connection pool.
    init_db(settings)
    # Select the realtime bus from configuration (Redis when
    # REDIS_URL is set, in-process otherwise) and install it as
    # the process-wide default the module-level ws_core.realtime
    # helpers delegate to.
    set_realtime_bus(create_realtime_bus(settings))

    container = DefaultContainer(
        settings=settings,
        event_bus=InProcessEventBus(),
        realtime_bus=get_realtime_bus(),
        session_factory=async_session_factory,
        rate_limiter=get_rate_limiter(),
    )

    app = FastAPI(
        title=title,
        version=version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Access logging as a pure ASGI middleware (see
    # _AccessLogMiddleware for why not BaseHTTPMiddleware).
    app.add_middleware(_AccessLogMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        max_age=600,
    )

    # Errors raised by the core's own /auth router. Domain
    # errors are registered by the plugin that raises them.
    register_domain_handlers(app, _CORE_ERRORS)

    # Core auth routes (/api/auth/...). Domain plugins mount
    # their own routers under /api from register().
    app.include_router(auth_router, prefix="/api")

    @app.on_event("startup")
    async def start_realtime_bus() -> None:
        """Start the realtime bus's background delivery.

        A no-op for the in-process bus; the Redis adapter
        uses it to spawn its pub/sub listener. Best-effort:
        a failure here must never block startup - the bus
        still delivers locally.
        """
        try:
            await get_realtime_bus().start()
        except Exception:  # pragma: no cover - defensive
            logger.exception("realtime_bus_start_failed")

    @app.on_event("shutdown")
    async def stop_realtime_bus() -> None:
        """Stop the realtime bus and release its resources.

        Cancels the Redis listener and closes the connection
        when the Redis adapter is in use; a no-op otherwise.
        """
        try:
            await get_realtime_bus().stop()
        except Exception:  # pragma: no cover - defensive
            logger.exception("realtime_bus_stop_failed")

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

    for plugin in plugins:
        plugin.register(app, container)

    return app
