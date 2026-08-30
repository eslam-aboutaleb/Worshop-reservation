"""Idempotent seed of demo workshops on first boot.

Invoked by the Dockerfile after ``alembic upgrade head`` and before
``uvicorn``. Safe to re-run; it inserts the demo rows only when the
``workshops`` table is empty.

Production deployments should not rely on this. Point new
deployments at a real content source or disable the seed via the
``RUN_SEED`` environment flag in the future.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from src.configuration.database import async_session_factory
from src.configuration.logging import configure_logging
from src.models.workshop import Workshop

logger = structlog.get_logger(__name__)


async def _seed() -> None:
    """Insert three demo workshops if the ``workshops`` table is empty.

    Idempotent: calling this on an already-seeded database is a no-op.
    The check is a single ``SELECT ... LIMIT 1``; for a multi-table
    seed (users, etc.) prefer an explicit guard column.
    """
    async with async_session_factory() as session:
        existing = (await session.execute(select(Workshop.id).limit(1))).first()
        if existing is not None:
            logger.info("seed.workshops_already_present", status="skipped")
            return

        now = datetime.now(UTC)
        demo = [
            Workshop(
                id=uuid.uuid4(),
                title="Async Python Patterns",
                starts_at=now + timedelta(days=7),
                max_capacity=20,
            ),
            Workshop(
                id=uuid.uuid4(),
                title="PostgreSQL for Developers",
                starts_at=now + timedelta(days=14),
                max_capacity=10,
            ),
            Workshop(
                id=uuid.uuid4(),
                title="Real-Time Web with Server-Sent Events",
                starts_at=now + timedelta(days=21),
                max_capacity=5,
            ),
        ]
        session.add_all(demo)
        await session.commit()
        logger.info("seed.inserted_workshops", count=len(demo))


def main() -> None:
    """Console-script entry point for ``python -m src.seed``."""
    configure_logging()
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
