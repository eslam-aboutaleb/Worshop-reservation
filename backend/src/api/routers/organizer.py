"""Organizer dashboard endpoints (roadmap 2.2).

Routes under the ``/organizer`` prefix:

* ``GET /stats`` - dashboard statistics for the
  calling organizer.

The route is gated by ``get_current_organizer``:
the configured super-admin, accounts with the
``organizer``/``admin`` platform role, and members
of an organization may through; a plain attendee
with no membership is rejected with 403.

The payload aggregates over the workshops the
caller may manage - the workshops owned by
organizations they belong to, or every workshop
for the super-admin (who may belong to no
organization). It carries total active bookings,
the count of upcoming published sessions, and a
per-workshop breakdown with booking counts and
the attendee list (including booking codes).

The attendee list is organizer-scoped by design:
the public workshop detail endpoint deliberately
exposes only the caller's own reservations, so
this is a separate query surface (see
``services/reservation_queries.list_workshop_attendees``).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import get_current_organizer
from src.configuration.database import get_db
from src.models.user import User
from src.schemas.organizer import OrganizerDashboardResponse
from src.services import organizer_service

router = APIRouter(prefix="/organizer", tags=["organizer"])


@router.get("/stats", response_model=OrganizerDashboardResponse)
async def get_organizer_stats(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> OrganizerDashboardResponse:
    """Return the calling organizer's dashboard statistics.

    Args:
        session: Active async database session.
        user: The calling organizer, resolved by
            ``get_current_organizer``.

    Returns:
        An ``OrganizerDashboardResponse`` with the
        total active bookings across the caller's
        workshops, the count of upcoming published
        sessions, and the per-workshop breakdown
        (booking counts and attendee lists with
        booking codes).

    Raises:
        HTTPException: 401 if the caller is not
            signed in; 403 if the caller is a plain
            attendee with no organization membership.
    """
    return await organizer_service.get_organizer_dashboard(session, user)
