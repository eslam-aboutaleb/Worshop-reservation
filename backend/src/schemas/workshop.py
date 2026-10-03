"""Pydantic schemas for the workshop API.

The two list/detail models differ in payload size only: the list
shape omits the embedded reservation list. Both use the same
``available_spots`` computation, which is **not** a column on
``workshops``; it is computed in ``services/workshop_service`` at
read time. Treat the field as derived-only and never persist it.

Lifecycle fields
----------------

``ends_at``, ``registration_closes_at`` and ``status`` (plan 0.1)
are carried on every response so clients can render "registration
closed" / "ended" states without a second round-trip. The create
and edit payloads validate that an explicit registration deadline
is not in the past.
"""

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from src.schemas.review import WorkshopReviewResponse


class WorkshopResponse(BaseModel):
    """Workshop summary returned by ``GET /api/workshops``.

    Attributes:
        id: Workshop UUID.
        title: Human-readable title.
        starts_at: Timezone-aware start timestamp.
        ends_at: Timezone-aware end timestamp, or ``None``.
        max_capacity: Total seats.
        available_spots: Seats not yet reserved (max_capacity minus
            the count of active reservations). Computed in the
            service layer.
        registration_closes_at: Booking deadline, or ``None``
            (meaning "closes when the workshop starts").
        status: Lifecycle state (``draft`` / ``published`` /
            ``cancelled``).
        organization_id: UUID of the owning organization, or
            ``None`` for platform-managed workshops.
        created_at: Workshop creation timestamp.
    """

    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    max_capacity: int = Field(gt=0)
    available_spots: int = Field(ge=0)
    registration_closes_at: datetime | None = None
    status: str = "published"
    organization_id: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkshopCreate(BaseModel):
    """Validated payload for an administrator-created workshop.

    Attributes:
        title: Human-readable title.
        starts_at: Timezone-aware start timestamp.
        ends_at: Optional timezone-aware end timestamp.
        max_capacity: Maximum number of active reservations.
        description: Long-form session description.
        category: Category label used for filtering.
        location: Where the session takes place.
        registration_closes_at: Optional booking deadline.
        organization_id: Optional owning organization. When
            set, the caller must be an administrator or a
            member of that organization (enforced in
            ``workshop_service.create_workshop``).
    """

    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    max_capacity: int = Field(gt=0)
    description: str = Field(default="", max_length=4000)
    category: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=200)
    registration_closes_at: AwareDatetime | None = None
    organization_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """Normalize title whitespace while rejecting an empty title."""
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("description", "category", "location")
    @classmethod
    def text_fields_strip_whitespace(cls, value: str) -> str:
        """Trim free-text metadata fields."""
        return value.strip()

    @field_validator("registration_closes_at")
    @classmethod
    def registration_closes_at_must_not_be_past(
        cls, value: datetime | None, info
    ) -> datetime | None:
        """Reject an explicit registration deadline in the past.

        A ``None`` deadline is allowed (it means "closes when the
        workshop starts"). An explicit deadline that has already
        passed would create a workshop nobody can book, so it is
        rejected at the boundary.
        """
        if value is None:
            return value
        from datetime import UTC, datetime as dt

        now = dt.now(UTC)
        # Compare on naive UTC instants: ``AwareDatetime`` values
        # carry an offset, so convert both sides to UTC first.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if value.astimezone(UTC) < now:
            raise ValueError("registration_closes_at must not be in the past")
        # The deadline must also precede the start time when both
        # are present; the database CHECK enforces this too, but
        # failing here yields a cleaner 422.
        starts_at = info.data.get("starts_at")
        if starts_at is not None:
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=UTC)
            if value.astimezone(UTC) > starts_at.astimezone(UTC):
                raise ValueError(
                    "registration_closes_at must not be after starts_at"
                )
        return value


class WorkshopUpdate(BaseModel):
    """Validated payload for editing a workshop (plan 1.1).

    Every field is optional; omitted fields keep their current
    value. ``status`` transitions are handled by the dedicated
    publish/cancel endpoints rather than a free-form edit, so
    editing cannot accidentally un-publish a live session.

    ``organization_id`` reassigns the workshop to another
    organization; the caller must be a member of the new
    organization (enforced in ``workshop_service.update_workshop``).
    Omitting it keeps the current ownership; clearing it is not
    supported through this payload.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_capacity: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    registration_closes_at: AwareDatetime | None = None
    organization_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str | None) -> str | None:
        """Normalize title whitespace while rejecting an empty title."""
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("description", "category", "location")
    @classmethod
    def text_fields_strip_whitespace(cls, value: str | None) -> str | None:
        """Trim free-text metadata fields."""
        if value is None:
            return value
        return value.strip()


class WorkshopListResponse(BaseModel):
    """Paginated workshop list envelope (plan 1.3).

    ``GET /api/workshops`` returns this envelope rather than a
    bare array so clients can render "load more" and know when
    the catalogue is exhausted. This is a breaking shape change
    from the previous bare-array response; the frontend is
    updated in the same release.

    Attributes:
        items: The page of workshop summaries.
        total: Total matching workshops (before pagination).
        limit: Page size that was applied.
        offset: Offset of the first item in ``items``.
    """

    items: list[WorkshopResponse]
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)


class WaitlistEntryResponse(BaseModel):
    """A place in line on a workshop's waitlist.

    Attributes:
        id: Waitlist entry UUID.
        workshop_id: Parent workshop UUID.
        status: ``active`` / ``promoted`` / ``cancelled``.
        created_at: When the caller joined the waitlist.
        promoted_at: When the entry was promoted, or ``None``.
    """

    id: uuid.UUID
    workshop_id: uuid.UUID
    status: str
    created_at: datetime
    promoted_at: datetime | None = None

    model_config = {"from_attributes": True}


class WaitlistJoinResponse(BaseModel):
    """Response for joining a waitlist.

    Attributes:
        entry: The waitlist entry (existing on an idempotent
            re-join).
        position: The caller's 1-based position in the queue.
        replayed: ``True`` when an existing active entry was
            returned instead of creating a new one.
    """

    entry: WaitlistEntryResponse
    position: int
    replayed: bool = False


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
        ends_at: Timezone-aware end timestamp, or ``None``.
        max_capacity: Total seats.
        available_spots: Seats not yet reserved.
        description: Long-form session description.
        category: Category label used for filtering.
        location: Where the session takes place.
        registration_closes_at: Booking deadline, or ``None``.
        status: Lifecycle state.
        organization_id: UUID of the owning organization, or
            ``None`` for platform-managed workshops.
        reservations: Active reservations visible to the caller
            (see class docstring).
        waitlist_position: The caller's 1-based position in the
            waitlist, or ``None`` when they are not waitlisted.
        rating_average: Average review rating (1..5), or
            ``None`` when the workshop has no reviews yet.
        rating_count: Number of reviews written for the
            workshop.
        reviews: The workshop's reviews, newest first,
            each carrying the reviewer's display name.
        created_at: Workshop creation timestamp.
    """

    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    max_capacity: int
    available_spots: int
    description: str = ""
    category: str = ""
    location: str = ""
    registration_closes_at: datetime | None = None
    status: str = "published"
    organization_id: uuid.UUID | None = None
    reservations: list[ReservationSummary]
    waitlist_position: int | None = None
    rating_average: float | None = None
    rating_count: int = 0
    reviews: list[WorkshopReviewResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}
