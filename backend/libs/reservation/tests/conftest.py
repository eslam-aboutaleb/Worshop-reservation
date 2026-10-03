"""Shared fixtures for the ws-reservation plugin test suite.

Self-contained: builds the composed app via the core
composition root with the reservation plugin mounted
(``create_app(settings, plugins=[ReservationPlugin()])``),
overrides the ``get_db`` dependency per test so the suite
runs against its own session factory, and exposes the
helpers the API-level tests need (``api_signup``,
``auth_headers``, ``session_factory``) as fixtures so the
suite has no import-time dependency on the application's
test package.

Environment defaults mirror ``run_tests.sh`` so the
suite is runnable standalone (``pytest
libs/reservation/tests``) as well as via the full
``./run_tests.sh``.

Fixture semantics mirror ``backend/tests/conftest.py``
exactly: the suite talks to a real Postgres because the
partial unique index and ``SELECT ... FOR UPDATE``
semantics are not reproducible on SQLite. The
``workshop`` fixture creates a capacity-1 workshop (the
concurrency tests depend on exactly one successful
reserve out of many concurrent attempts), ``workshop_id``
creates a separate capacity-3 workshop for API behavior
tests, and every fixture cleans up after itself so
concurrent test execution against the same database is
still safe.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

# The test environment must be in place before
# ``src.configuration.settings`` is imported: that
# module instantiates and caches the ``Settings``
# object at import time (``configure_ws_core(get_settings())``
# at module bottom), and a cached instance would
# otherwise read the repository ``.env`` - whose
# ``ADMIN_EMAIL`` belongs to the developer, not the
# test suite - instead of the test defaults below.
# Environment variables take precedence over the
# ``.env`` file, so setting them first keeps the
# suite self-contained and runnable standalone.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://workshop_user:workshop_pass@localhost:5432/workshop_reservations",
)
os.environ.setdefault("CORS_ORIGINS", '["http://localhost"]')
os.environ.setdefault(
    "AUTH_SECRET_KEY",
    "test-secret-key-at-least-32-characters-long-for-tests",
)
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
# The test stack runs on plain HTTP, so the session
# cookie must not carry the Secure attribute.
os.environ.setdefault("COOKIE_SECURE", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from ws_core.app import create_app  # noqa: E402
from ws_core.db.engine import get_db  # noqa: E402
from ws_core.events import EventBus  # noqa: E402
from ws_core.events.in_process import InProcessEventBus  # noqa: E402
from ws_core.rate_limit import reset_rate_limiter  # noqa: E402
from ws_core.realtime import set_realtime_bus  # noqa: E402
from ws_core.realtime.in_process import InProcessRealtimeBus  # noqa: E402
from ws_reservation import ReservationPlugin  # noqa: E402
from ws_reservation.models import Workshop  # noqa: E402

from src.configuration.settings import get_settings  # noqa: E402

_engine = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

# The composed app: core infrastructure plus the
# reservation domain plugin.
app = create_app(get_settings(), plugins=[ReservationPlugin()])


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


@pytest.fixture(autouse=True)
def _isolate_sse_state() -> None:
    """Install a fresh in-process realtime bus between tests.

    The default realtime bus holds subscriber state (queues
    registered by SSE endpoints) that survives across tests.
    A test that subscribes via ``subscribe_global`` and exits
    without unsubscribing would otherwise leak a queue that
    ``publish`` would try to write to, eventually causing the
    runner to hang. Replacing the whole bus gives every test
    a clean subscriber set.
    """
    set_realtime_bus(InProcessRealtimeBus())


@pytest.fixture(autouse=True)
def _isolate_rate_limiter() -> None:
    """Reset the process-wide auth rate limiter between tests.

    ``get_rate_limiter()`` is a lazy singleton whose failure
    budget would otherwise leak from one test into the next: a
    test that makes several failed login attempts could lock
    out an unrelated test that happens to reuse the same
    ``(client IP, email)`` key. Resetting before each test
    makes every case start with a clean budget.
    """
    reset_rate_limiter()


@pytest_asyncio.fixture
async def workshop() -> AsyncGenerator[Workshop, None]:
    """Insert a fresh capacity-1 workshop for the test and clean it up afterwards.

    Uses its own session (not the ``session`` fixture) so concurrent
    workers in the concurrency test get their own connections.
    """
    new_id, instance = await _create_workshop(title="Concurrency test workshop", max_capacity=1)
    try:
        yield instance
    finally:
        await _delete_workshop(new_id)


@pytest_asyncio.fixture
async def workshop_id() -> AsyncGenerator[str, None]:
    """Create and clean up a workshop sized for API behavior tests."""
    new_id, _ = await _create_workshop(title="API test workshop", max_capacity=3)
    try:
        yield str(new_id)
    finally:
        await _delete_workshop(new_id)


async def _create_workshop(*, title: str, max_capacity: int) -> tuple[uuid.UUID, Workshop]:
    """Insert a test workshop and return its id plus refreshed ORM row."""
    new_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with _session_factory() as session:
        instance = Workshop(
            id=new_id,
            title=title,
            starts_at=now + timedelta(days=1),
            max_capacity=max_capacity,
        )
        session.add(instance)
        await session.commit()
        await session.refresh(instance)
        return new_id, instance


async def _delete_workshop(workshop_id: uuid.UUID) -> None:
    """Remove a test workshop and its cascaded rows."""
    async with _session_factory() as session:
        await session.execute(text("DELETE FROM workshops WHERE id = :id"), {"id": workshop_id})
        await session.commit()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an httpx AsyncClient wired to the composed app.

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


@pytest.fixture
def event_bus() -> EventBus:
    """A fresh in-process event bus for direct service calls."""
    return InProcessEventBus()


@pytest.fixture
def session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the test session factory for direct DB access."""
    return _session_factory


@pytest_asyncio.fixture
async def api_signup(client: AsyncClient):
    """Return a callable that signs up a fresh account through the public API.

    Returns:
        An async callable ``(email=..., full_name=...) -> (token, email)``.
        Creating a reservation requires an authenticated account, so most
        write-path tests start here.

    Note that the ``client`` fixture keeps cookies between calls, so
    once ``api_signup`` has run every later request on that client
    carries the new account's session cookie unless the test either
    passes an explicit ``Authorization`` header or calls
    ``client.cookies.clear()`` to force the anonymous path.
    """

    async def _api_signup(
        *, email: str | None = None, full_name: str = "Test Attendee"
    ) -> tuple[str, str]:
        email = email or f"user_{uuid.uuid4().hex}@example.com"
        response = await client.post(
            "/api/auth/signup",
            json={"full_name": full_name, "email": email, "password": "Password123!"},
        )
        assert response.status_code == 201, response.text
        return response.json()["access_token"], email

    return _api_signup


@pytest.fixture
def auth_headers():
    """Return the bearer-header builder for API calls."""

    def _auth_headers(token: str, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", **extra}

    return _auth_headers
