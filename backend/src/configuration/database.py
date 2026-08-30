"""Async SQLAlchemy engine, session factory, and FastAPI DB dependency.

Why a single module-level engine?
---------------------------------

A long-lived ``AsyncEngine`` is the recommended pattern for async
SQLAlchemy: the engine owns a connection pool, and constructing a new
engine per request would defeat the pool. Tests that need full
isolation construct their own engine (see ``tests/conftest.py``).

Session lifecycle
-----------------

``async_session_factory`` is configured with ``expire_on_commit=False``
so ORM instances remain usable after ``session.commit()``;
otherwise attribute access would issue a lazy refresh on a closed
transaction.

``get_db`` is a FastAPI dependency generator: it yields a session for
the request handler and is guaranteed to close it via the ``finally``
block, even if the handler raises.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.configuration.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=settings.database_pool_pre_ping,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a per-request ``AsyncSession`` and close it afterwards.

    Used as a FastAPI dependency (``session: AsyncSession = Depends(get_db)``).
    The session is opened in a context manager so the connection is
    always returned to the pool; the ``finally`` block is a belt-and-
    suspenders guard in case the async-context-manager path is ever
    bypassed.

    Yields:
        An ``AsyncSession`` bound to the shared engine.
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
