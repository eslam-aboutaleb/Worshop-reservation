"""Organization and organization-membership SQLAlchemy models.

Organizations (roadmap 2.1) are the unit of ownership for the
organizer platform: a workshop belongs to at most one organization,
and the members of that organization may manage it. The models in
this file are deliberately small - the interesting behavior (slug
generation, creator promotion, authorization) lives in
``src/services/organization_service.py`` and ``src/auth.py``.

Membership roles
------------------

``owner``  - the account that created the organization. There is
    exactly one owner per organization in practice (the creator);
    the role exists as a distinct value so future plans can add
    owner-transfer or multiple-owner support without a migration.
``member`` - a plain member of the organization. Members may
    manage the organization's workshops but cannot administer the
    membership itself (no add/remove endpoint exists yet).

Why a composite primary key?
------------------------------

``organization_memberships`` is keyed on ``(user_id,
organization_id)`` rather than a surrogate id: a user holds at
most one role per organization, so the pair *is* the natural key,
and the plan's uniqueness constraint ``unique (user_id,
organization_id)`` is satisfied by the primary key itself. The
membership response schema therefore exposes the pair instead of a
single ``id``.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ws_core.db.base import Base

MEMBERSHIP_ROLE_OWNER = "owner"
MEMBERSHIP_ROLE_MEMBER = "member"


class Organization(Base):
    """An organizing account that owns workshops.

    Attributes:
        id: Server-generated UUID primary key.
        name: Human-readable organization name (1..200 chars).
        slug: Unique, URL-safe identifier derived from ``name``
            (see ``organization_service._slugify``). Uniqueness is
            enforced by the database (``uq_organizations_slug``).
        created_at: Server timestamp at insert time.
        memberships: The accounts that belong to this organization.
        workshops: The workshops owned by this organization.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    memberships = relationship("OrganizationMembership", back_populates="organization")
    workshops = relationship("Workshop", back_populates="organization")
    follows = relationship("OrganizationFollow", back_populates="organization")


class OrganizationMembership(Base):
    """A user's role within an organization.

    Attributes:
        user_id: Foreign key to ``users.id`` (``ON DELETE CASCADE``
            so deleting an account drops its memberships). Part of
            the composite primary key.
        organization_id: Foreign key to ``organizations.id``
            (``ON DELETE CASCADE`` so deleting an organization drops
            its memberships). Part of the composite primary key.
        role: ``owner`` or ``member``.
        created_at: Server timestamp at insert time.
        user: Back-reference to the member ``User``.
        organization: Back-reference to the parent ``Organization``.
    """

    __tablename__ = "organization_memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MEMBERSHIP_ROLE_MEMBER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization", back_populates="memberships")

    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'member')",
            name="ck_organization_memberships_role",
        ),
    )


class OrganizationFollow(Base):
    """A user's follow of an organization (roadmap 2.3).

    Following is the attendee-side counterpart to membership:
    a follower sees the organization's workshops in the
    ``following=true`` filter on the discovery list, but holds
    no management rights. A user follows an organization at
    most once, so the ``(user_id, organization_id)`` pair is
    the natural key - the primary key itself satisfies the
    plan's uniqueness constraint, mirroring
    ``OrganizationMembership``.

    A member (owner or plain member) cannot follow their own
    organization - they already have a stronger relationship -
    so the service layer rejects that case before an insert.

    Attributes:
        user_id: Foreign key to ``users.id`` (``ON DELETE
            CASCADE`` so deleting an account drops its
            follows). Part of the composite primary key.
        organization_id: Foreign key to ``organizations.id``
            (``ON DELETE CASCADE`` so deleting an organization
            drops its followers). Part of the composite primary
            key.
        created_at: Server timestamp at insert time.
        user: Back-reference to the following ``User``.
        organization: Back-reference to the followed
            ``Organization``.
    """

    __tablename__ = "organization_follows"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user = relationship("User", back_populates="follows")
    organization = relationship("Organization", back_populates="follows")
