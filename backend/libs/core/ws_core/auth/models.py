"""User account SQLAlchemy model.

Personal accounts let attendees see and cancel their own reservations
across devices. Authentication is handled by
:mod:`ws_core.auth` (password hashing, JWT issuance, and the
FastAPI dependencies); this module is concerned only with
persistence.

Password storage
----------------

``password_hash`` stores a salted Argon2id PHC string produced by
:func:`ws_core.auth.password.hash_password`. Each value embeds its
algorithm version, cost parameters, salt, and digest so the format
remains self-describing.

The plaintext password is never written to a log and never appears in
a response schema. ``UserResponse`` exposes only the public fields.

Platform roles
----------------

``role`` is the platform-wide role introduced by the organizations
plan (roadmap 2.1): ``attendee`` (the default), ``organizer`` (may
manage workshops), or ``admin``. It is distinct from the
environment-configured super-admin flag checked by
:func:`ws_core.auth.dependencies.is_admin`: ``is_admin`` remains the
super-admin signal, while ``role`` gates organizer-platform access.
Organization creators are promoted to ``organizer`` when they create
their first organization.

Why this model lives in ws-core
---------------------------------

The account table is shared infrastructure: the auth router, the
reservation ownership rules, and every plugin's "who is the caller"
question all read the same row. Keeping ``User`` in core (next to
``Base``) lets a plugin define its own models with relationships back
to ``User`` on the single shared metadata registry.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ws_core.db.base import Base

USER_ROLE_ATTENDEE = "attendee"
USER_ROLE_ORGANIZER = "organizer"
USER_ROLE_ADMIN = "admin"


class User(Base):
    """A personal account that can own reservations across devices.

    Attributes:
        id: Server-generated UUID primary key.
        full_name: Display name captured at signup (1..200 chars).
        email: Lowercased, unique email address. The unique constraint
            is enforced by the database so a duplicate signup returns
            a 409 (the ``users_email_key`` index).
        password_hash: Salted Argon2id PHC string (see module docstring).
        role: Platform role - ``attendee``, ``organizer``, or
            ``admin``. Defaults to ``attendee``; creating an
            organization promotes the creator to ``organizer``.
        created_at: Server timestamp at insert time.
        reservations: All reservations owned by this account, loaded
            by the SQLAlchemy relationship only when explicitly
            accessed.
        memberships: This account's organization memberships.
        follows: The organizations this account follows.
        reviews: The reviews this account has written.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=USER_ROLE_ATTENDEE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    reservations = relationship("Reservation", back_populates="user")
    waitlist_entries = relationship("WaitlistEntry", back_populates="user")
    memberships = relationship("OrganizationMembership", back_populates="user")
    follows = relationship("OrganizationFollow", back_populates="user")
    reviews = relationship("Review", back_populates="user")

    __table_args__ = (
        CheckConstraint(
            "role IN ('attendee', 'organizer', 'admin')",
            name="ck_users_role",
        ),
    )
