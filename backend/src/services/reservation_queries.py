"""Database queries used by reservation workflows."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.idempotency_key import IdempotencyKey
from src.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from src.models.user import User
from src.models.waitlist_entry import (
    WAITLIST_STATUS_ACTIVE,
    WaitlistEntry,
)
from src.models.workshop import Workshop


async def find_idempotent_reservation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    idempotency_key: str,
    user_id: uuid.UUID,
) -> Reservation | None:
    """Return the reservation this caller previously created for a key.

    The lookup is scoped to ``user_id``. The idempotency key is a
    client-supplied secret rather than an identity, so keying only on
    ``(key, workshop_id)`` would let any account that reused another
    caller's key read back that caller's reservation, attendee name
    and email included.

    Keys expire (plan 0.4): the join filters on ``expires_at`` so a
    key older than its TTL no longer replays, even though the row
    may still be present before the startup sweep removes it.
    """
    statement = (
        select(Reservation)
        .join(IdempotencyKey, IdempotencyKey.reservation_id == Reservation.id)
        .where(IdempotencyKey.key == idempotency_key)
        .where(IdempotencyKey.workshop_id == workshop_id)
        .where(IdempotencyKey.user_id == user_id)
        .where(IdempotencyKey.expires_at > datetime.now(UTC))
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def count_active_reservations(session: AsyncSession, workshop_id: uuid.UUID) -> int:
    """Count active reservations for a workshop."""
    statement = (
        select(func.count())
        .select_from(Reservation)
        .where(Reservation.workshop_id == workshop_id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
    )
    return int((await session.execute(statement)).scalar_one())


async def list_user_reservations(
    session: AsyncSession, user: User
) -> list[tuple[Reservation, str]]:
    """Return a user's reservations with their workshop titles."""
    statement = (
        select(Reservation, Workshop.title)
        .join(Workshop, Workshop.id == Reservation.workshop_id)
        .where(Reservation.user_id == user.id)
        .order_by(Reservation.created_at.desc())
    )
    return list((await session.execute(statement)).all())


async def list_user_waitlist_entries(
    session: AsyncSession, user: User
) -> list[tuple[WaitlistEntry, str]]:
    """Return a user's active waitlist entries with workshop titles.

    Only ``active`` entries are returned: promoted and
    cancelled places in line are historical and belong on
    the tickets/account view only insofar as they produced
    a reservation. Ordered oldest-first so the account
    view can present the queue in the order the user
    joined it.
    """
    statement = (
        select(WaitlistEntry, Workshop.title)
        .join(Workshop, Workshop.id == WaitlistEntry.workshop_id)
        .where(WaitlistEntry.user_id == user.id)
        .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
        .order_by(WaitlistEntry.created_at.asc())
    )
    return list((await session.execute(statement)).all())


async def find_active_waitlist_entry(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID,
) -> WaitlistEntry | None:
    """Return the caller's active waitlist entry, if any."""
    statement = (
        select(WaitlistEntry)
        .where(WaitlistEntry.workshop_id == workshop_id)
        .where(WaitlistEntry.user_id == user_id)
        .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def get_waitlist_position(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int | None:
    """Return the caller's 1-based waitlist position, or ``None``.

    The position is the count of active entries created strictly
    before the caller's, plus one. ``None`` means the caller holds
    no active place in line (never joined, already promoted, or
    already left).
    """
    entry = await find_active_waitlist_entry(session, workshop_id, user_id)
    if entry is None:
        return None
    statement = (
        select(func.count())
        .select_from(WaitlistEntry)
        .where(WaitlistEntry.workshop_id == workshop_id)
        .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
        .where(WaitlistEntry.created_at < entry.created_at)
    )
    ahead = int((await session.execute(statement)).scalar_one())
    return ahead + 1


async def count_active_waitlist_entries(
    session: AsyncSession, workshop_id: uuid.UUID
) -> int:
    """Count active waitlist entries for a workshop."""
    statement = (
        select(func.count())
        .select_from(WaitlistEntry)
        .where(WaitlistEntry.workshop_id == workshop_id)
        .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
    )
    return int((await session.execute(statement)).scalar_one())


async def list_workshop_attendees(
    session: AsyncSession,
    workshop_id: uuid.UUID,
) -> list[Reservation]:
    """Return every reservation ever made for a workshop.

    Unlike the workshop detail endpoint - which
    deliberately exposes only the caller's own
    active reservations - this is the organizer-side
    attendee list (roadmap 2.2): every reservation,
    active and cancelled, oldest first, so the
    organizer can see who is coming and who released
    their seat. Each row carries the attendee name,
    email and booking code the dashboard renders.

    Args:
        session: Active async database session.
        workshop_id: Workshop whose attendees to list.

    Returns:
        All ``Reservation`` rows for the workshop,
        ordered by ``created_at`` ascending.
    """
    statement = (
        select(Reservation)
        .where(Reservation.workshop_id == workshop_id)
        .order_by(Reservation.created_at.asc())
    )
    return list((await session.execute(statement)).scalars().all())


async def count_active_reservations_for_workshops(
    session: AsyncSession,
    workshop_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Count active reservations per workshop.

    A single grouped query replaces one ``COUNT(*)``
    per workshop on the organizer dashboard.

    Args:
        session: Active async database session.
        workshop_ids: Workshops to count for. An
            empty list short-circuits to an empty
            map without touching the database.

    Returns:
        A mapping of workshop id to its number of
        active reservations (workshops with no
        active reservations are absent from the map).
    """
    if not workshop_ids:
        return {}
    statement = (
        select(Reservation.workshop_id, func.count(Reservation.id))
        .where(Reservation.workshop_id.in_(workshop_ids))
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        .group_by(Reservation.workshop_id)
    )
    rows = (await session.execute(statement)).all()
    return {workshop_id: int(count) for workshop_id, count in rows}
