"""Pydantic schemas for the reservation API.

Three request shapes and three response shapes cover the entire
reservation surface. ``MyReservationResponse`` is the one with extra
context used only by the signed-in account history view
(``GET /api/reservations/me``); everywhere else the client sees the
plain ``ReservationResponse``.

Email handling
--------------

Both ``ReservationCreate.attendee_email`` and the auth schemas
lowercase + trim the email at the Pydantic boundary. The database
relies on that normalization for case-insensitive uniqueness.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.common import NormalizedEmail


class ReservationCreate(BaseModel):
    """Request body for ``POST /api/workshops/{id}/reservations``.

    Attributes:
        attendee_name: Full name of the attendee (1..200 chars).
        attendee_email: Valid email (normalized to lowercase, trimmed).
            The database partial unique index treats this as the
            natural key for "is this person already booked?".
    """

    attendee_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Full name of the attendee",
    )
    attendee_email: NormalizedEmail = Field(
        ...,
        description="Valid email address of the attendee",
    )


class ReservationResponse(BaseModel):
    """Standard reservation payload returned by create / list / cancel.

    Attributes:
        id: Reservation UUID.
        workshop_id: Parent workshop UUID.
        attendee_name: Attendee's full name.
        attendee_email: Attendee's email (already lowercased).
        booking_code: Server-generated confirmation code
            (``WKS-`` + 6 chars). This is the check-in lookup
            key and the value printed on the ticket.
        status: ``"active"`` or ``"cancelled"``.
        created_at: Insert timestamp.
        cancelled_at: Cancellation timestamp, or ``None`` while active.
    """

    id: uuid.UUID
    workshop_id: uuid.UUID
    attendee_name: str
    attendee_email: str
    booking_code: str = ""
    status: str
    created_at: datetime
    cancelled_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReservationCancelResponse(BaseModel):
    """Payload returned by ``DELETE /api/reservations/{id}``.

    Attributes:
        id: Reservation UUID.
        status: Always ``"cancelled"`` on success (a second cancel
            returns 200 with the existing cancelled row).
        cancelled_at: Timestamp at which the reservation was cancelled.
    """

    id: uuid.UUID
    status: str
    cancelled_at: datetime | None

    model_config = {"from_attributes": True}


class MyReservationResponse(ReservationResponse):
    """Reservation enriched with its workshop title.

    Used by ``GET /api/reservations/me`` so the signed-in account
    dashboard can show a human-friendly label without a second
    round-trip per row.

    Attributes:
        workshop_title: Title of the parent workshop, joined in
            ``reservation_queries.list_user_reservations``.
    """

    workshop_title: str
