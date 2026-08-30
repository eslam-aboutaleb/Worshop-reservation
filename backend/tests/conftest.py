"""Pytest configuration: live PostgreSQL with a per-test fresh workshop row.

The suite talks to a real Postgres because the partial unique
index and SELECT ... FOR UPDATE semantics are not reproducible on
SQLite. Each test gets its own workshop via the `workshop` fixture,
so concurrent test execution against the same database is still
safe.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://workshop_user:workshop_pass@localhost:5432/workshop_reservations",
)
os.environ.setdefault("CORS_ORIGINS", '["http://localhost"]')
os.environ.setdefault(
    "AUTH_SECRET_KEY",
    "test-secret-key-at-least-32-characters-long-for-tests",
)
os.environ.setdefault("ADMIN_EMAIL", "eslamehababoutaleb@gmail.com")

from src.configuration.database import get_db  # noqa: E402
from src.main import app  # noqa: E402
from src.models.workshop import Workshop  # noqa: E402

_engine = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


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
    """Drop leftover SSE subscribers between tests.

    The realtime module keeps module-level state
    (``_global_subscribers``) that survives across tests. A test
    that subscribes via ``subscribe_global`` and exits without
    unsubscribing would otherwise leak a queue that ``publish``
    would try to write to, eventually causing the runner to hang.
    """
    from src.realtime import _global_subscribers

    _global_subscribers.clear()


@pytest_asyncio.fixture
async def workshop() -> AsyncGenerator[Workshop, None]:
    """Insert a fresh workshop for the test and clean it up afterwards.

    Uses its own session (not the `session` fixture) so concurrent
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
    """Yield an httpx AsyncClient wired to the FastAPI app.

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


from unittest.mock import MagicMock  # noqa: E402

from src.auth import create_access_token  # noqa: E402
from src.models.user import User  # noqa: E402


@pytest_asyncio.fixture
async def attendee() -> AsyncGenerator[User, None]:
    new_id = uuid.uuid4()
    async with _session_factory() as session:
        instance = User(id=new_id, email=f"attendee_{new_id}@example.com", hashed_password="dummy")
        session.add(instance)
        await session.commit()
        await session.refresh(instance)
        try:
            yield instance
        finally:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": new_id})
            await session.commit()


@pytest.fixture
def attendee_token(attendee: User) -> str:
    return create_access_token(attendee.id)


@pytest_asyncio.fixture
async def attendee_client(
    client: AsyncClient, attendee_token: str
) -> AsyncGenerator[AsyncClient, None]:
    client.headers["Authorization"] = f"Bearer {attendee_token}"
    yield client


@pytest_asyncio.fixture
async def admin() -> AsyncGenerator[User, None]:
    new_id = uuid.uuid4()
    async with _session_factory() as session:
        instance = User(id=new_id, email=os.environ["ADMIN_EMAIL"], hashed_password="dummy")
        session.add(instance)
        await session.commit()
        await session.refresh(instance)
        try:
            yield instance
        finally:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": new_id})
            await session.commit()


@pytest.fixture
def admin_token(admin: User) -> str:
    return create_access_token(admin.id)


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, admin_token: str) -> AsyncGenerator[AsyncClient, None]:
    client.headers["Authorization"] = f"Bearer {admin_token}"
    yield client


@pytest.fixture
def mock_broker(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    import src.realtime

    mock = MagicMock()
    monkeypatch.setattr(src.realtime, "publish", mock)
    return mock
