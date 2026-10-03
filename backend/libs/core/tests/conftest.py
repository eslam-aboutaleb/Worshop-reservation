"""Shared fixtures for the ws-core test suite.

Self-contained: the suite registers its own
:class:`~ws_core.config.CoreSettings` implementation
with ws-core (reading the same environment variables
the application's ``Settings`` reads), builds an app
via :func:`ws_core.app.create_app` with **no**
plugins (the core surface is the ``/auth`` router,
``/health`` and the middleware), and overrides the
``get_db`` dependency per test so the suite runs
against its own session factory.

Environment defaults mirror ``run_tests.sh`` so the
suite is runnable standalone (``pytest
libs/core/tests``) as well as via the full
``./run_tests.sh``.
"""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ws_core.app import create_app
from ws_core.config import configure
from ws_core.db.engine import get_db
from ws_core.rate_limit import reset_rate_limiter
from ws_core.realtime import set_realtime_bus
from ws_core.realtime.in_process import InProcessRealtimeBus

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://workshop_user:workshop_pass@localhost:5432/workshop_reservations",
)
os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key-at-least-32-characters-long-for-tests")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost"]')


class _CoreTestSettings:
    """Minimal :class:`CoreSettings` for the core suite.

    Reads the same environment variables as the
    application's ``Settings`` so the core tests
    exercise the same configuration the composed app
    does, without importing the application.
    """

    database_url = os.environ["DATABASE_URL"]
    database_pool_size = 10
    database_max_overflow = 20
    database_pool_pre_ping = True
    database_echo = False
    log_level = "INFO"
    log_format = "json"
    auth_secret_key = os.environ["AUTH_SECRET_KEY"]
    admin_email = os.environ.get("ADMIN_EMAIL") or None
    access_token_expire_days = 30
    auth_max_attempts = 5
    auth_lockout_seconds = 900
    cookie_name = "workshop_access_token"
    cookie_secure = False
    cookie_samesite = "lax"
    redis_url = ""
    server_host = "0.0.0.0"
    server_port = 8000
    cors_origins = ["http://localhost"]
    sse_heartbeat_seconds = 15.0


configure(_CoreTestSettings())

_engine = create_async_engine(_CoreTestSettings.database_url, echo=False, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

# The core app: no plugins, so only the core surface
# (/auth, /health, middleware) is mounted.
app = create_app(_CoreTestSettings())


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw async session for direct service tests.

    The session is rolled back on teardown so a failed test cannot
    leave the connection in a half-committed state that bleeds into
    the next test.
    """
    async with _session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx AsyncClient wired to the core app.

    The dependency override is installed before and removed after
    the yield, with a try/finally so a setup failure still cleans up
    the global ``app.dependency_overrides`` map (which would
    otherwise leak into the next test).
    """

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with _session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_db] = _get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_sse_state() -> None:
    """Install a fresh in-process realtime bus between tests.

    The default realtime bus holds subscriber state (queues
    registered by SSE endpoints) that survives across tests.
    Replacing the whole bus gives every test a clean subscriber set.
    """
    set_realtime_bus(InProcessRealtimeBus())


@pytest.fixture(autouse=True)
def _isolate_rate_limiter() -> None:
    """Reset the process-wide auth rate limiter between tests.

    ``get_rate_limiter()`` is a lazy singleton whose failure
    budget would otherwise leak from one test into the next.
    Resetting before each test makes every case start with a clean budget.
    """
    reset_rate_limiter()
