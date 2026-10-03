"""Pydantic schemas for the organizations API.

Organizations (roadmap 2.1) are created by a signed-in
account, which becomes the organization's first ``owner``
and is promoted to the ``organizer`` platform role. The
create response therefore carries both the organization and
the creator's membership so the client can render the
"you own this organization" state without a second
round-trip.

Slug handling
----------------

``OrganizationCreate.slug`` is optional; when omitted the
service derives a slug from ``name`` (see
``organization_service._slugify``) and appends a random
suffix on collision. The database enforces uniqueness either
way (``uq_organizations_slug``).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class OrganizationCreate(BaseModel):
    """Validated payload for creating an organization.

    Attributes:
        name: Human-readable organization name (1..200 chars).
        slug: Optional URL-safe identifier. When omitted, one
            is derived from ``name``. Uniqueness is enforced
            by the database.
    """

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        """Normalize name whitespace while rejecting an empty name."""
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value

    @field_validator("slug")
    @classmethod
    def slug_strip_whitespace(cls, value: str | None) -> str | None:
        """Trim the optional slug."""
        return value.strip() if value is not None else value


class OrganizationMembershipResponse(BaseModel):
    """A user's membership in an organization.

    The membership is keyed on the ``(user_id, organization_id)``
    pair, so the response exposes both ids rather than a single
    surrogate ``id``.

    Attributes:
        user_id: Member account UUID.
        organization_id: Organization UUID.
        role: ``owner`` or ``member``.
        created_at: When the membership was created.
    """

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationResponse(BaseModel):
    """Organization payload returned by the organizations API.

    Attributes:
        id: Organization UUID.
        name: Human-readable name.
        slug: Unique URL-safe identifier.
        created_at: Organization creation timestamp.
        membership: The requesting user's membership. Populated
            on creation (the creator becomes the owner); ``None``
            for plain reads that do not resolve a membership.
        followers_count: Number of accounts following the
            organization (roadmap 2.3). Computed at read time;
            a freshly created organization has none.
    """

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    membership: OrganizationMembershipResponse | None = None
    followers_count: int = Field(default=0, ge=0)

    model_config = {"from_attributes": True}
