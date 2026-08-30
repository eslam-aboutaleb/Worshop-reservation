"""Pydantic schemas for the workshop API.

The two list/detail models differ in payload size only: the list
shape omits the embedded reservation list. Both use the same
``available_spots`` computation, which is **not** a column on
``workshops``; it is computed in ``services/workshop_service`` at
read time. Treat the field as derived-only and never persist it.
"""

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class WorkshopResponse(BaseModel):
    """Workshop summary returned by ``GET /api/workshops``.

    Attributes:
        id: Workshop UUID.
        title: Human-readable title.
        starts_at: Timezone-aware start timestamp.
        max_capacity: Total seats.
        available_spots: Seats not yet reserved (max_capacity minus
            the count of active reservations). Computed in the
            service layer.
        created_at: Workshop creation timestamp.
    """

    id: uuid.UUID
    title: str
    starts_at: datetime
    max_capacity: int = Field(gt=0)
    available_spots: int = Field(ge=0)
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkshopCreate(BaseModel):
    """Validated payload for an administrator-created workshop."""

    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime
    max_capacity: int = Field(gt=0)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """Normalize title whitespace while rejecting an empty title."""
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class ReservationSummary(BaseModel):
    """Compact view of a reservation embedded in workshop details.

    Attributes:
        id: Reservation UUID.
        attendee_name: Attendee's full name.
        attendee_email: Attendee's email (already lowercased).
        created_at: Reservation creation timestamp.
    """

    id: uuid.UUID
    attendee_name: str
    attendee_email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkshopDetailResponse(BaseModel):
    """Detailed workshop payload including its active reservations.

    When the request is made by a signed-in user, the embedded
    ``reservations`` list contains only that user's own active
    bookings (see ``workshop_service.get_workshop_detail``). For
    anonymous requests the list is always empty - the brief calls
    for a private-by-default model where only your own bookings
    are visible.

    Attributes:
        id: Workshop UUID.
        title: Human-readable title.
        starts_at: Timezone-aware start timestamp.
        max_capacity: Total seats.
        available_spots: Seats not yet reserved.
        reservations: Active reservations visible to the caller
            (see class docstring).
        created_at: Workshop creation timestamp.
    """

    id: uuid.UUID
    title: str
    starts_at: datetime
    max_capacity: int
    available_spots: int
    reservations: list[ReservationSummary]
    created_at: datetime

    model_config = {"from_attributes": True}
