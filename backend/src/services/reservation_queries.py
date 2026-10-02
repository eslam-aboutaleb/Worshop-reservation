"""Database queries used by reservation workflows."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.idempotency_key import IdempotencyKey
from src.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from src.models.user import User
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
    """
    statement = (
        select(Reservation)
        .join(IdempotencyKey, IdempotencyKey.reservation_id == Reservation.id)
        .where(IdempotencyKey.key == idempotency_key)
        .where(IdempotencyKey.workshop_id == workshop_id)
        .where(IdempotencyKey.user_id == user_id)
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
