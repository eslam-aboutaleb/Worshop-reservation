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

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class Workshop(Base):
    """A bookable event with a fixed capacity.

    Attributes:
        id: Server-generated UUID primary key.
        title: Human-readable event name (1..200 chars).
        starts_at: Timezone-aware start timestamp.
        max_capacity: Maximum number of active reservations. Enforced
            ``> 0`` by the database ``CHECK`` constraint.
        created_at: Server timestamp at insert time.
        reservations: All reservations (active and cancelled) attached
            to this workshop. Loaded eagerly via ``selectin`` so detail
            endpoints do not N+1.
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
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
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

    __table_args__ = (CheckConstraint("max_capacity > 0", name="ck_workshops_positive_capacity"),)
