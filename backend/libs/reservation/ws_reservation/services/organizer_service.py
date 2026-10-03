"""Read-side service for the organizer dashboard (roadmap 2.2).

The dashboard aggregates over the workshops the caller
may manage:

* a member (owner or plain member) sees the workshops
  owned by organizations they belong to, and
* the configured super-admin - who may belong to no
  organization - sees **every** workshop so the
  dashboard stays useful for platform administration.

The payload carries summary statistics (total active
bookings across those workshops, count of upcoming
published sessions) plus a per-workshop breakdown with
its booking count and full attendee list (including
booking codes, the check-in lookup key).

Authorization itself happens in the
``get_current_organizer`` dependency; a plain attendee
with no membership is rejected with 403 before this
service runs.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import is_admin

from ws_reservation.models.organization import OrganizationMembership
from ws_reservation.models.workshop import WORKSHOP_STATUS_PUBLISHED, Workshop
from ws_reservation.schemas.organizer import (
    OrganizerAttendeeSummary,
    OrganizerDashboardResponse,
    OrganizerWorkshopStats,
)
from ws_reservation.services.reservation_queries import (
    count_active_reservations_for_workshops,
    list_workshop_attendees,
)

logger = structlog.get_logger(__name__)


async def _own_workshop_ids(
    session: AsyncSession,
    user,
) -> list[uuid.UUID] | None:
    """Return the ids of the workshops the caller manages.

    Args:
        session: Active async database session.
        user: The caller, already authorized by
            ``get_current_organizer``.

    Returns:
        The list of workshop ids the caller may
        manage, or ``None`` when the caller is the
        configured super-admin (meaning "all
        workshops"). A non-admin organizer with no
        memberships gets an empty list.
    """
    if is_admin(user):
        return None
    statement = select(OrganizationMembership.organization_id).where(
        OrganizationMembership.user_id == user.id
    )
    organization_ids = list((await session.execute(statement)).scalars().all())
    if not organization_ids:
        return []
    workshop_statement = select(Workshop.id).where(Workshop.organization_id.in_(organization_ids))
    return list((await session.execute(workshop_statement)).scalars().all())


async def get_organizer_dashboard(
    session: AsyncSession,
    user,
) -> OrganizerDashboardResponse:
    """Build the organizer dashboard payload for ``user``.

    Runs three queries regardless of catalogue size:
    one for the caller's workshops, one grouped count
    of active reservations, and one for the attendee
    lists - no per-workshop round trips.

    Args:
        session: Active async database session.
        user: The calling organizer, resolved by
            ``get_current_organizer``.

    Returns:
        An ``OrganizerDashboardResponse`` with the
        total active bookings, the count of upcoming
        published sessions, and the per-workshop
        breakdown ordered by ``starts_at`` ascending.
    """
    workshop_ids = await _own_workshop_ids(session, user)
    if workshop_ids is not None and not workshop_ids:
        return OrganizerDashboardResponse(
            total_bookings=0,
            upcoming_sessions=0,
            workshops=[],
        )

    workshop_statement = select(Workshop).order_by(
        Workshop.starts_at.asc(), Workshop.created_at.asc()
    )
    if workshop_ids is not None:
        workshop_statement = workshop_statement.where(Workshop.id.in_(workshop_ids))
    workshops = list((await session.execute(workshop_statement)).scalars().all())

    ids = [workshop.id for workshop in workshops]
    booking_counts = await count_active_reservations_for_workshops(session, ids)

    now = datetime.now(UTC)
    total_bookings = sum(booking_counts.values())
    upcoming_sessions = sum(
        1
        for workshop in workshops
        if workshop.status == WORKSHOP_STATUS_PUBLISHED and workshop.starts_at >= now
    )

    stats: list[OrganizerWorkshopStats] = []
    for workshop in workshops:
        attendees = await list_workshop_attendees(session, workshop.id)
        stats.append(
            OrganizerWorkshopStats(
                workshop_id=workshop.id,
                title=workshop.title,
                starts_at=workshop.starts_at,
                ends_at=workshop.ends_at,
                status=workshop.status,
                booking_count=booking_counts.get(workshop.id, 0),
                attendees=[
                    OrganizerAttendeeSummary(
                        reservation_id=reservation.id,
                        attendee_name=reservation.attendee_name,
                        attendee_email=reservation.attendee_email,
                        booking_code=reservation.booking_code,
                        status=reservation.status,
                        created_at=reservation.created_at,
                    )
                    for reservation in attendees
                ],
            )
        )

    logger.info(
        "organizer.dashboard_viewed",
        user_id=str(user.id),
        workshop_count=len(workshops),
        total_bookings=total_bookings,
    )
    return OrganizerDashboardResponse(
        total_bookings=total_bookings,
        upcoming_sessions=upcoming_sessions,
        workshops=stats,
    )
