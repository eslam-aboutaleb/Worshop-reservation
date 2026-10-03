"""Idempotent seed of demo workshops on first boot.

Invoked by the Dockerfile after ``alembic upgrade head`` and
before ``uvicorn``. Safe to re-run; it inserts the demo rows
only when the ``workshops`` table is empty.

Production deployments should not rely on this. Point new
deployments at a real content source or disable the seed via
the ``RUN_SEED`` environment flag in the future.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
import ws_core.db.engine as db_engine
from sqlalchemy import select
from ws_core.db.engine import init_db
from ws_core.logging import configure_logging

from src.configuration.settings import get_settings
from ws_reservation.models import Workshop

logger = structlog.get_logger(__name__)


async def _seed() -> None:
    """Insert three demo workshops if the ``workshops`` table is empty.

    Idempotent: calling this on an already-seeded database is a no-op.
    The check is a single ``SELECT ... LIMIT 1``; for a multi-table
    seed (users, etc.) prefer an explicit guard column.
    """
    async with db_engine.async_session_factory() as session:
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
                ends_at=now + timedelta(days=7, hours=2),
                max_capacity=20,
                description=(
                    "A hands-on tour of asyncio: tasks, queues, "
                    "backpressure, and structured concurrency. Leave with "
                    "patterns you can apply the same week."
                ),
                category="Engineering",
                location="Room 1 / Virtual",
                registration_closes_at=now + timedelta(days=7) - timedelta(hours=1),
            ),
            Workshop(
                id=uuid.uuid4(),
                title="PostgreSQL for Developers",
                starts_at=now + timedelta(days=14),
                ends_at=now + timedelta(days=14, hours=3),
                max_capacity=10,
                description=(
                    "Indexes, isolation levels, and query planning "
                    "demystified. Bring a laptop and a slow query."
                ),
                category="Data",
                location="Room 2",
                registration_closes_at=now + timedelta(days=14) - timedelta(hours=2),
            ),
            Workshop(
                id=uuid.uuid4(),
                title="Real-Time Web with Server-Sent Events",
                starts_at=now + timedelta(days=21),
                ends_at=now + timedelta(days=21, hours=2),
                max_capacity=5,
                description=(
                    "Build a live-updating UI with a single SSE channel: "
                    "heartbeats, reconnects, and a privacy-first event design."
                ),
                category="Engineering",
                location="Room 3 / Virtual",
                registration_closes_at=now + timedelta(days=21) - timedelta(hours=1),
            ),
        ]
        session.add_all(demo)
        await session.commit()
        logger.info("seed.inserted_workshops", count=len(demo))


def main() -> None:
    """Console-script entry point for ``python -m src.seed``."""
    configure_logging()
    # The seed script runs standalone (before uvicorn), so it must
    # build the process-wide engine itself; create_app() is not on
    # this path.
    init_db(get_settings())
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
