"""Async SQLAlchemy engine, session factory, and FastAPI DB dependency.

Why a factory rather than a module-level engine?
--------------------------------------------------

The original code built the engine at import time, which
hard-wired the database URL to whatever environment was
active when the module was first imported. That made the
engine impossible to reconfigure (tests, multi-tenant
bootstraps) and forced every importer to pay for a
connection pool it might not use.

:func:`init_db` is the single place the engine is built.
The composition root (``create_app``) calls it with the
resolved settings; anything that needs a session
(:func:`get_db`, the seed script) uses the module-level
:data:`async_session_factory` that ``init_db`` populates.
The factory is idempotent: calling it again disposes the
prior engine and builds a fresh one, so re-configuration
never leaks a pool.

Session lifecycle
-----------------

``async_session_factory`` is configured with
``expire_on_commit=False`` so ORM instances remain usable
after ``session.commit()``; otherwise attribute access
would issue a lazy refresh on a closed transaction.

``get_db`` is a FastAPI dependency generator: it yields a
session for the request handler and is guaranteed to close
it via the ``finally`` block, even if the handler raises.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ws_core.config import CoreSettings

engine: AsyncEngine | None = None
"""Process-wide async engine, populated by :func:`init_db`."""

async_session_factory: async_sessionmaker[AsyncSession] | None = None
"""Process-wide session factory, populated by :func:`init_db`."""


def init_db(settings: CoreSettings) -> None:
    """Build (or rebuild) the process-wide engine and session factory.

    Idempotent: a prior engine is disposed before the new one
    is built, so repeated calls (a re-configured composition
    root, a test that rebuilds settings) never leak a
    connection pool.

    Args:
        settings: The resolved settings supplying the
            database URL and pool tuning.
    """
    global engine, async_session_factory
    if engine is not None:
        # Dispose the prior pool synchronously; the async
        # dispose is scheduled on the next event loop, which
        # is acceptable for a re-configuration path that runs
        # once at boot.
        old_engine = engine
        engine = None
        async_session_factory = None
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                # No loop running (boot path): dispose inline.
                asyncio.run(old_engine.dispose())
            else:
                # A loop is running (re-config path): schedule
                # the dispose without blocking the caller.
                loop.create_task(old_engine.dispose())
        except Exception:  # pragma: no cover - defensive
            pass

    new_engine = create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=settings.database_pool_pre_ping,
    )
    engine = new_engine
    async_session_factory = async_sessionmaker(
        new_engine,
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

    Raises:
        RuntimeError: If :func:`init_db` has not been called.
            The composition root calls it before serving, so
            this only fires when the dependency is used
            without a bootstrapped engine.
    """
    if async_session_factory is None:
        raise RuntimeError(
            "ws_core.db.init_db(settings) must be called before the get_db dependency is used."
        )
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
