"""Workshop SQLAlchemy model.

The single largest table in the system. Each row represents a bookable
event with a fixed capacity. Capacity is enforced *outside* the table
by ``services/reservation_service.create_reservation``; this file
only carries the read-side schema and a ``CHECK`` constraint that
``max_capacity`` is positive.

Capacity vs. active reservations
--------------------------------

The number of currently active reservations for a workshop is not
stored here. It is computed on demand via a correlated subquery (see
``services/workshop_service.list_workshops``) or a plain ``COUNT(*)``
inside the reservation transaction. A counter column is
deliberately not maintained: every read would need to stay in sync
with ``reservations`` updates, and the cost of a subquery on a list
endpoint is negligible at the project's expected scale.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

WORKSHOP_STATUS_DRAFT = "draft"
WORKSHOP_STATUS_PUBLISHED = "published"
WORKSHOP_STATUS_CANCELLED = "cancelled"


class Workshop(Base):
    """A bookable event with a fixed capacity.

    Attributes:
        id: Server-generated UUID primary key.
        title: Human-readable event name (1..200 chars).
        starts_at: Timezone-aware start timestamp.
        ends_at: Timezone-aware end timestamp, or ``None`` when
            the session has no declared end.
        max_capacity: Maximum number of active reservations. Enforced
            ``> 0`` by the database ``CHECK`` constraint.
        registration_closes_at: Timezone-aware deadline after which
            new reservations are refused. ``None`` means "closes when
            the workshop starts" (the application resolves the default).
        status: Lifecycle state - ``draft``, ``published``, or
            ``cancelled``. Only ``published`` workshops are bookable
            or listable; the others are 404-shaped to avoid leaking
            their existence.
        organization_id: Optional foreign key to ``organizations.id``
            (``ON DELETE SET NULL``). A workshop without an owning
            organization is managed by the platform administrator
            only; members of the owning organization may manage
            workshops attached to it.
        created_at: Server timestamp at insert time.
        reservations: All reservations (active and cancelled) attached
            to this workshop. Loaded eagerly via ``selectin`` so detail
            endpoints do not N+1.
        organization: Back-reference to the owning ``Organization``,
            or ``None`` for platform-managed workshops.
        reviews: Post-workshop reviews (roadmap 2.4), loaded by
            the SQLAlchemy relationship only when explicitly
            accessed; the detail endpoint queries them through
            ``services/review_service`` instead.
    """

    __tablename__ = "workshops"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        default="",
    )
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    location: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    registration_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WORKSHOP_STATUS_PUBLISHED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    reservations = relationship(
        "Reservation",
        back_populates="workshop",
        lazy="selectin",
    )
    waitlist_entries = relationship(
        "WaitlistEntry",
        back_populates="workshop",
        lazy="selectin",
    )
    organization = relationship("Organization", back_populates="workshops")
    reviews = relationship("Review", back_populates="workshop")

    __table_args__ = (
        CheckConstraint("max_capacity > 0", name="ck_workshops_positive_capacity"),
        CheckConstraint(
            "status IN ('draft', 'published', 'cancelled')",
            name="ck_workshops_status",
        ),
        CheckConstraint(
            "registration_closes_at IS NULL OR registration_closes_at <= starts_at",
            name="ck_workshops_registration_window",
        ),
    )
