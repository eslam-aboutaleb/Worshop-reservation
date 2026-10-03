"""Waitlist-entry SQLAlchemy model.

A waitlist entry reserves a *place in line*, not a seat.
When a reservation is cancelled, the oldest active entry is
promoted inside the same transaction (see
``services/reservation_service.cancel_reservation``), which
makes promotion exactly-once by construction: the seat
release and the promotion commit or roll back together.

Why a separate table?
----------------------

The ``reservations`` table records seats that exist *now*;
a waitlist entry records a claim on a seat that does not
exist yet. Keeping them separate means the partial unique
index on ``reservations`` (one active booking per person per
workshop) stays the single source of truth for "who is
booked", while the waitlist's own partial unique index
(``uq_active_waitlist_entry``) enforces "one place in line
per person per workshop".

Status transitions
-------------------

``active``   - waiting; eligible for promotion.
``promoted`` - a reservation was created for this user from
    this entry; ``promoted_at`` and ``reservation_id`` are
    set. The entry is no longer eligible.
``cancelled`` - the user left the waitlist, or the workshop
    was deleted. No longer eligible.

Only ``active`` entries are promoted, and the promotion
query locks them with ``SELECT ... FOR UPDATE`` ordered by
``created_at`` so two concurrent cancels cannot both promote
the same entry.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ws_core.db.base import Base

WAITLIST_STATUS_ACTIVE = "active"
WAITLIST_STATUS_PROMOTED = "promoted"
WAITLIST_STATUS_CANCELLED = "cancelled"


class WaitlistEntry(Base):
    """A place in line for a seat that is currently taken.

    Attributes:
        id: Server-generated UUID primary key.
        workshop_id: Foreign key to ``workshops.id``. ``ON DELETE
            CASCADE`` so removing a workshop removes its waitlist.
        user_id: Foreign key to ``users.id``. ``ON DELETE CASCADE``
            so deleting an account drops its waitlist places.
        status: ``active`` / ``promoted`` / ``cancelled``.
        created_at: Server timestamp at insert time; the FIFO
            ordering key for promotion.
        promoted_at: Set when the entry is promoted; ``None``
            otherwise.
        reservation_id: The reservation created by promotion;
            ``None`` until (and unless) promotion happens.
        workshop: Back-reference to the parent ``Workshop``.
        user: Back-reference to the owning ``User``.
    """

    __tablename__ = "waitlist_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workshops.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WAITLIST_STATUS_ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reservations.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    workshop = relationship("Workshop", back_populates="waitlist_entries")
    user = relationship("User", back_populates="waitlist_entries")

    __table_args__ = (
        Index(
            "uq_active_waitlist_entry",
            "workshop_id",
            "user_id",
            unique=True,
            postgresql_where=(status == WAITLIST_STATUS_ACTIVE),
        ),
        Index("ix_waitlist_entries_workshop_created", "workshop_id", "created_at"),
        Index("ix_waitlist_entries_reservation", "reservation_id"),
    )
