"""User account SQLAlchemy model.

Personal accounts let attendees see and cancel their own reservations
across devices. Authentication is handled by ``src/auth.py``; this
module is concerned only with persistence.

Password storage
----------------

``password_hash`` stores a salted Argon2id PHC string produced by
``auth.hash_password``. Each value embeds its algorithm version, cost
parameters, salt, and digest so the format remains self-describing.

The plaintext password is never written to a log and never appears in
a response schema. ``UserResponse`` exposes only the public fields.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class User(Base):
    """A personal account that can own reservations across devices.

    Attributes:
        id: Server-generated UUID primary key.
        full_name: Display name captured at signup (1..200 chars).
        email: Lowercased, unique email address. The unique constraint
            is enforced by the database so a duplicate signup returns
            a 409 (the ``users_email_key`` index).
        password_hash: Salted Argon2id PHC string (see module docstring).
        created_at: Server timestamp at insert time.
        reservations: All reservations owned by this account, loaded
            by the SQLAlchemy relationship only when explicitly
            accessed.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    reservations = relationship("Reservation", back_populates="user")
