"""Idempotency-key SQLAlchemy model.

Stores the mapping ``(key, workshop_id) -> reservation_id`` so that a
retried ``POST /api/workshops/{id}/reservations`` with the same
``Idempotency-Key`` header returns the original reservation rather
than creating a duplicate. This is the *first* of two layers of
idempotency: the *second* is the partial unique index
``uq_active_reservation`` on the ``reservations`` table itself.

Why a dedicated table?
----------------------

A client that retries with the same key must always get the same
answer, even if the workshop is now full, the email was changed, or
the original reservation was cancelled. None of that information
is trivially derivable from the ``reservations`` table alone. The
``idempotency_keys`` table is the canonical record of "the request
that was made" and decouples the API contract from the data model.

Key scope
---------

The primary key is the composite ``(key, workshop_id)`` deliberately,
not just ``key``. The same client-generated key may legitimately be
used to reserve a seat in two different workshops; those are
independent requests and should not collide.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class IdempotencyKey(Base):
    """A client-supplied idempotency key scoped to a workshop.

    Attributes:
        key: Client-supplied idempotency key (opaque string, max
            255 chars to match the cap on the request header).
        workshop_id: Foreign key to ``workshops.id``. ``ON DELETE
            CASCADE`` so removing a workshop cleans up its
            idempotency records automatically.
        reservation_id: Foreign key to the ``Reservation`` produced by
            the first successful request carrying this key. Cascade-
            deleted with the reservation, although in practice
            cancellations do not remove idempotency records.
        created_at: Server timestamp at insert time. Not currently
            surfaced in any endpoint; reserved for future "show the
            caller's recent requests" features.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workshops.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
