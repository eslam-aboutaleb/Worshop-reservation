"""Pydantic schemas for the organizer dashboard (roadmap 2.2).

The dashboard is a read-only aggregate over the
workshops the caller may manage: the workshops owned
by organizations they belong to, or - for the
configured super-admin, who may belong to no
organization - every workshop. The payload is shaped
so the organizer UI can render summary cards (total
bookings, upcoming sessions) and a per-workshop
breakdown with its attendee list in a single
round-trip.

Booking counts cover **active** reservations only:
a cancelled booking is no longer a booking. The
attendee list, by contrast, includes every
reservation ever made for the workshop (active and
cancelled) with its status, so an organizer can see
who attended and who released their seat; each row
carries the booking code used for check-in.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OrganizerAttendeeSummary(BaseModel):
    """One reservation on an organizer's workshop.

    Attributes:
        reservation_id: Reservation UUID.
        attendee_name: Attendee's full name.
        attendee_email: Attendee's email (already
            lowercased).
        booking_code: Server-generated confirmation
            code (``WKS-`` + 6 chars); the check-in
            lookup key.
        status: ``"active"`` or ``"cancelled"``.
        created_at: Reservation creation timestamp.
    """

    reservation_id: uuid.UUID
    attendee_name: str
    attendee_email: str
    booking_code: str = ""
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizerWorkshopStats(BaseModel):
    """Per-workshop breakdown on the organizer dashboard.

    Attributes:
        workshop_id: Workshop UUID.
        title: Human-readable title.
        starts_at: Timezone-aware start timestamp.
        ends_at: Timezone-aware end timestamp, or ``None``.
        status: Lifecycle state (``draft`` / ``published`` /
            ``cancelled``).
        booking_count: Number of **active** reservations.
        attendees: Every reservation ever made for the
            workshop (any status), oldest first, each
            with its booking code.
    """

    workshop_id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    status: str
    booking_count: int = Field(ge=0)
    attendees: list[OrganizerAttendeeSummary] = Field(default_factory=list)


class OrganizerDashboardResponse(BaseModel):
    """Payload returned by ``GET /api/organizer/stats``.

    Attributes:
        total_bookings: Total active reservations across
            the caller's workshops.
        upcoming_sessions: Count of the caller's
            published workshops that have not started
            yet.
        workshops: The caller's own workshops ordered by
            ``starts_at`` ascending, each with its
            booking count and attendee list.
    """

    total_bookings: int = Field(ge=0)
    upcoming_sessions: int = Field(ge=0)
    workshops: list[OrganizerWorkshopStats] = Field(default_factory=list)
