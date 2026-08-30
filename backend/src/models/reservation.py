"""Reservation SQLAlchemy model.

The reservations table is the heart of the system. Every seat booking
is a row here, and the **partial unique index**
``uq_active_reservation (workshop_id, attendee_email) WHERE status='active'``
is the central invariant the rest of the service depends on.

Why a partial unique index?
---------------------------

A "user cannot reserve the same workshop twice" rule can be expressed
three ways:

* application-level check (racy under concurrency);
* a non-partial unique constraint (wrong: a user should be able to
  reserve, cancel, and re-reserve the same workshop);
* a partial unique index keyed on ``status = 'active'`` (correct,
  race-free, enforced by the database).

The index is hand-written in the migration; autogenerate historically
struggles with the ``postgresql_where`` clause, so do not let it
regenerate that constraint. See ``migrations/versions/0001_initial.py``.

Email is lowercased and trimmed at the Pydantic boundary
(``schemas/reservation.ReservationCreate``), so equality on
``attendee_email`` is case-insensitive in practice.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

RESERVATION_STATUS_ACTIVE = "active"
RESERVATION_STATUS_CANCELLED = "cancelled"


class Reservation(Base):
    """A booking linking an attendee to a workshop.

    Attributes:
        id: Server-generated UUID primary key.
        workshop_id: Foreign key to ``workshops.id`` (``ON DELETE
            CASCADE`` so removing a workshop removes its reservations).
        user_id: Optional foreign key to ``users.id``. ``NULL`` for
            legacy/anonymous reservations. ``ON DELETE SET NULL``
            preserves the reservation record when an account is deleted.
        attendee_name: Free-form name captured at booking time.
        attendee_email: Lowercased/trimmed email; the partial unique
            index treats this as a case-insensitive natural key.
        status: ``"active"`` or ``"cancelled"``. The partial unique
            index applies only when ``status = 'active'``.
        created_at: Server timestamp at insert time.
        cancelled_at: Timestamp set by ``cancel_reservation``; ``NULL``
            for active reservations.
        workshop: Back-reference to the parent ``Workshop`` row.
        user: Back-reference to the owning ``User`` (or ``NULL``).
    """

    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workshops.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    attendee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    attendee_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RESERVATION_STATUS_ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    workshop = relationship("Workshop", back_populates="reservations")
    user = relationship("User", back_populates="reservations")

    __table_args__ = (
        Index(
            "uq_active_reservation",
            "workshop_id",
            "attendee_email",
            unique=True,
            postgresql_where=(status == RESERVATION_STATUS_ACTIVE),
        ),
    )
