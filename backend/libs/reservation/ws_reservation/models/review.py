"""Review SQLAlchemy model.

Reviews (roadmap 2.4) let an attendee rate and comment on a
workshop **after** attending it. Eligibility is enforced by
the service layer (``services/review_service.py``): the
caller must have held a reservation for the workshop - in
any status, a cancelled booking still counts as having
attended - and the workshop's session must have ended
(``ends_at``, falling back to ``starts_at`` when the
workshop declares no explicit end).

One review per user per workshop is the invariant. The
application checks for an existing row first so the caller
gets a friendly 4xx with a stable error code; the unique
constraint ``uq_reviews_workshop_user`` (created by
migration ``0013_reviews``) is the race-free backstop.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ws_core.db.base import Base


class Review(Base):
    """A post-workshop rating and optional comment.

    Attributes:
        id: Server-generated UUID primary key.
        workshop_id: Foreign key to ``workshops.id`` (``ON
            DELETE CASCADE`` so removing a workshop removes
            its reviews).
        user_id: Foreign key to ``users.id`` (``ON DELETE
            CASCADE`` so deleting an account drops its
            reviews).
        rating: Integer rating in 1..5, enforced by the
            ``ck_reviews_rating`` CHECK constraint and by
            ``ReviewCreate`` at the schema boundary.
        text: Optional free-form comment.
        created_at: Server timestamp at insert time; the
            "newest first" ordering key for the detail view.
        workshop: Back-reference to the reviewed ``Workshop``.
        user: Back-reference to the reviewing ``User``.
    """

    __tablename__ = "reviews"

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
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    workshop = relationship("Workshop", back_populates="reviews")
    user = relationship("User", back_populates="reviews")

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        UniqueConstraint("workshop_id", "user_id", name="uq_reviews_workshop_user"),
    )
