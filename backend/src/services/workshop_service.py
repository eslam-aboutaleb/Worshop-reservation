"""Read-side service functions for the workshop surface.

This module is responsible for the **list** and **detail** queries
exposed under ``/api/workshops``. It deliberately does not write:
all mutations live in ``reservation_service`` so the row-locked
capacity check is in one place.

``available_spots`` is a *computed* field, not a column. It is
computed on every read instead of maintaining a counter so the
read path is trivially consistent with the reservations table.
The cost is a ``COUNT(*)`` (or correlated subquery) per row, which
is negligible at the project's expected scale.
"""

import uuid

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import WorkshopHasActiveReservationsError, WorkshopNotFoundError
from src.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from src.models.workshop import Workshop
from src.realtime import publish
from src.schemas.workshop import (
    ReservationSummary,
    WorkshopCreate,
    WorkshopDetailResponse,
    WorkshopResponse,
)
from src.services.reservation_queries import count_active_reservations

logger = structlog.get_logger(__name__)


async def list_workshops(session: AsyncSession) -> list[WorkshopResponse]:
    """Return every workshop with its current available-spot count.

    The active-reservation count is computed via a correlated subquery
    so the list endpoint stays to a single round trip even for a
    large workshop catalogue.

    Args:
        session: Active async database session.

    Returns:
        A list of ``WorkshopResponse`` ordered by ``starts_at``
        ascending, so the soonest session comes first. Ties on
        ``starts_at`` fall back to ``created_at`` for a stable
        order across refreshes.
    """
    active_count = (
        select(func.count(Reservation.id))
        .where(Reservation.workshop_id == Workshop.id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        .correlate(Workshop)
        .scalar_subquery()
    )
    stmt = select(
        Workshop, (Workshop.max_capacity - active_count).label("available_spots")
    ).order_by(Workshop.starts_at.asc(), Workshop.created_at.asc())
    result = await session.execute(stmt)
    rows = result.all()
    return [
        WorkshopResponse(
            id=workshop.id,
            title=workshop.title,
            starts_at=workshop.starts_at,
            max_capacity=workshop.max_capacity,
            available_spots=max(available_spots, 0),
            created_at=workshop.created_at,
        )
        for workshop, available_spots in rows
    ]


async def create_workshop(session: AsyncSession, payload: WorkshopCreate) -> WorkshopResponse:
    """Persist and broadcast a new bookable workshop."""
    workshop = Workshop(
        title=payload.title.strip(),
        starts_at=payload.starts_at,
        max_capacity=payload.max_capacity,
    )
    session.add(workshop)
    await session.commit()
    await session.refresh(workshop)
    response = WorkshopResponse(
        id=workshop.id,
        title=workshop.title,
        starts_at=workshop.starts_at,
        max_capacity=workshop.max_capacity,
        available_spots=workshop.max_capacity,
        created_at=workshop.created_at,
    )
    await publish(
        workshop.id,
        {"workshop_id": str(workshop.id), "type": "workshop_created"},
    )
    logger.info("workshop.created", workshop_id=str(workshop.id))
    return response


async def delete_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> None:
    """Delete an unbooked workshop and notify connected catalogues."""
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    active_count = await session.scalar(
        select(func.count(Reservation.id))
        .where(Reservation.workshop_id == workshop_id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
    )
    if active_count:
        raise WorkshopHasActiveReservationsError(str(workshop_id))

    await session.execute(delete(Workshop).where(Workshop.id == workshop_id))
    await session.commit()
    await publish(workshop_id, {"workshop_id": str(workshop_id), "type": "workshop_deleted"})
    logger.info("workshop.deleted", workshop_id=str(workshop_id))


async def get_workshop_detail(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> WorkshopDetailResponse:
    """Return a single workshop with its active reservations.

    Privacy model: the embedded ``reservations`` list is filtered
    to active rows owned by ``user_id`` when a signed-in caller
    is identified; anonymous calls always see an empty list. This
    matches the schema's documentation and the brief's privacy
    expectations (a workshop page shows "you are booked", not
    "everyone is booked").

    Args:
        session: Active async database session.
        workshop_id: Workshop to fetch.
        user_id: Optional account UUID; when provided, only that
            account's own active reservations are returned.

    Returns:
        A ``WorkshopDetailResponse`` with the computed
        ``available_spots`` and the caller's visible reservations.

    Raises:
        WorkshopNotFoundError: If no workshop exists with ``workshop_id``.
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id))
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    active_count = await count_active_reservations(session, workshop_id)
    available_spots = max(workshop.max_capacity - active_count, 0)

    if user_id is None:
        visible_reservations: list[Reservation] = []
    else:
        visible_reservations = list(
            (
                await session.execute(
                    select(Reservation)
                    .where(Reservation.workshop_id == workshop_id)
                    .where(Reservation.user_id == user_id)
                    .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
                )
            )
            .scalars()
            .all()
        )

    return WorkshopDetailResponse(
        id=workshop.id,
        title=workshop.title,
        starts_at=workshop.starts_at,
        max_capacity=workshop.max_capacity,
        available_spots=available_spots,
        reservations=[
            ReservationSummary(
                id=reservation.id,
                attendee_name=reservation.attendee_name,
                attendee_email=reservation.attendee_email,
                created_at=reservation.created_at,
            )
            for reservation in visible_reservations
        ],
        created_at=workshop.created_at,
    )
