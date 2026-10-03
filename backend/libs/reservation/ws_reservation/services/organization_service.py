"""Write-side service functions for organizations.

Organizations (roadmap 2.1) are created by a signed-in
account. Creating an organization is a single transaction
that:

1. inserts the ``organizations`` row (with a unique slug),
2. inserts the creator's ``organization_memberships`` row
   with role ``owner``, and
3. promotes the creator's ``users.role`` to ``organizer``.

All three steps commit or roll back together, so an
organization always has exactly one owner and the owner
always holds the organizer role.

This module deliberately does **not** import
:mod:`src.auth`: the authorization decisions live in the
``get_current_organizer`` dependency and the workshop
service, and importing ``src.auth`` here would create a
circular import (``src.auth`` imports the models this
module persists).

Following (roadmap 2.3)
-------------------------

``follow_organization`` / ``unfollow_organization``
implement the attendee-side follow surface. Both are
idempotent: following an already-followed organization
returns the existing row (HTTP 200 rather than 201),
and unfollowing an organization the caller never
followed is a no-op. A member cannot follow their own
organization - they already hold a stronger
relationship - so that case raises
``CannotFollowOwnOrganizationError`` before any insert.
"""

import re
import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth.models import USER_ROLE_ORGANIZER
from ws_core.errors import (
    CannotFollowOwnOrganizationError,
    OrganizationNotFoundError,
)

from ws_reservation.models.organization import (
    MEMBERSHIP_ROLE_OWNER,
    Organization,
    OrganizationFollow,
    OrganizationMembership,
)
from ws_reservation.schemas.organization import (
    OrganizationCreate,
    OrganizationMembershipResponse,
    OrganizationResponse,
)

logger = structlog.get_logger(__name__)

# Slugs are derived from the name by lowercasing and
# replacing runs of non-alphanumerics with a single hyphen.
_SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LENGTH = 180


def _slugify(name: str) -> str:
    """Derive a URL-safe slug from an organization name.

    Args:
        name: The organization's display name.

    Returns:
        A lowercase, hyphen-separated slug. Falls back to
        ``"organization"`` when the name contains no
        alphanumeric characters.
    """
    slug = _SLUG_SEPARATOR.sub("-", name.lower()).strip("-")
    slug = slug[:_SLUG_MAX_LENGTH].rstrip("-")
    return slug or "organization"


async def _unique_slug(
    session: AsyncSession,
    name: str,
    requested: str | None,
) -> str:
    """Return a slug that is not already taken.

    The requested slug (or one derived from ``name``) is
    used as the base. On collision a short random suffix is
    appended, which makes concurrent creations with the
    same name practically collision-free without a retry
    loop against the database.

    Args:
        session: Active async database session.
        name: The organization's display name.
        requested: The slug the caller asked for, or
            ``None`` to derive one from ``name``.

    Returns:
        A slug that does not match any existing
        organization.
    """
    base = _slugify(requested if requested is not None else name)
    candidate = base
    while True:
        exists = (
            await session.execute(
                select(Organization.id).where(Organization.slug == candidate)
            )
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        candidate = f"{base}-{uuid.uuid4().hex[:8]}"


async def is_organization_member(
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> bool:
    """Return whether ``user_id`` holds a membership in ``organization_id``.

    Any membership role (``owner`` or ``member``) counts;
    callers that need to distinguish roles should read the
    membership row directly.

    Args:
        session: Active async database session.
        user_id: Account to check.
        organization_id: Organization to check against.

    Returns:
        ``True`` if the user is a member of the organization.
    """
    membership = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    return membership is not None


async def create_organization(
    session: AsyncSession,
    payload: OrganizationCreate,
    user,
) -> OrganizationResponse:
    """Create an organization and make ``user`` its owner.

    The creator receives an ``owner`` membership and their
    platform role is promoted to ``organizer`` in the same
    transaction, so the new owner can immediately manage
    workshops attached to the organization.

    Args:
        session: Active async database session.
        payload: Validated creation payload (name and
            optional slug).
        user: The signed-in account creating the
            organization.

    Returns:
        The new organization and the creator's ``owner``
        membership.
    """
    slug = await _unique_slug(session, payload.name, payload.slug)
    organization = Organization(name=payload.name, slug=slug)
    session.add(organization)
    # Flush to populate ``organization.id`` before the
    # membership row references it.
    await session.flush()
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=organization.id,
        role=MEMBERSHIP_ROLE_OWNER,
    )
    session.add(membership)
    user.role = USER_ROLE_ORGANIZER
    await session.commit()
    await session.refresh(organization)
    await session.refresh(membership)
    logger.info(
        "organization.created",
        organization_id=str(organization.id),
        owner_id=str(user.id),
    )
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        created_at=organization.created_at,
        membership=OrganizationMembershipResponse.model_validate(membership),
    )


async def count_followers(
    session: AsyncSession,
    organization_id: uuid.UUID,
) -> int:
    """Count the accounts following an organization.

    Args:
        session: Active async database session.
        organization_id: Organization to count followers for.

    Returns:
        The number of rows in ``organization_follows``
        for the organization.
    """
    statement = (
        select(func.count())
        .select_from(OrganizationFollow)
        .where(OrganizationFollow.organization_id == organization_id)
    )
    return int((await session.execute(statement)).scalar_one())


async def find_follow(
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> OrganizationFollow | None:
    """Return the caller's follow of an organization, if any.

    Args:
        session: Active async database session.
        user_id: Account that may be following.
        organization_id: Organization that may be followed.

    Returns:
        The ``OrganizationFollow`` row, or ``None`` when
        the user does not follow the organization.
    """
    statement = select(OrganizationFollow).where(
        OrganizationFollow.user_id == user_id,
        OrganizationFollow.organization_id == organization_id,
    )
    return (await session.execute(statement)).scalar_one_or_none()


def _organization_response(
    organization: Organization,
    followers_count: int,
) -> OrganizationResponse:
    """Build an ``OrganizationResponse`` without a membership.

    Used by the follow surface, where the caller is by
    definition **not** a member (members cannot follow
    their own organization), so the embedded membership
    is always ``None``.

    Args:
        organization: The organization row.
        followers_count: Current follower count.

    Returns:
        The organization payload with ``membership=None``.
    """
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        created_at=organization.created_at,
        membership=None,
        followers_count=followers_count,
    )


async def follow_organization(
    session: AsyncSession,
    organization_id: uuid.UUID,
    user,
) -> tuple[OrganizationResponse, bool]:
    """Follow an organization as ``user`` (roadmap 2.3).

    Idempotent: a caller who already follows the
    organization gets the existing follow back with
    ``replayed=True`` (the router maps that to HTTP 200
    instead of 201). Members of the organization are
    rejected - they already have a stronger relationship
    than a follow - and an unknown organization id is a
    404 so the API does not leak which organizations
    exist.

    Args:
        session: Active async database session.
        organization_id: Organization to follow.
        user: The signed-in account following.

    Returns:
        The organization payload (with its refreshed
        ``followers_count``) and whether the follow
        already existed.

    Raises:
        OrganizationNotFoundError: 404 if no organization
            has this id.
        CannotFollowOwnOrganizationError: 409 if the
            caller is a member of the organization.
    """
    organization = (
        await session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if organization is None:
        raise OrganizationNotFoundError(str(organization_id))

    if await is_organization_member(session, user.id, organization_id):
        raise CannotFollowOwnOrganizationError(str(organization_id))

    existing = await find_follow(session, user.id, organization_id)
    if existing is not None:
        return (
            _organization_response(
                organization, await count_followers(session, organization_id)
            ),
            True,
        )

    follow = OrganizationFollow(
        user_id=user.id,
        organization_id=organization_id,
    )
    session.add(follow)
    await session.commit()
    await session.refresh(organization)
    logger.info(
        "organization.followed",
        organization_id=str(organization_id),
        user_id=str(user.id),
    )
    return (
        _organization_response(
            organization, await count_followers(session, organization_id)
        ),
        False,
    )


async def unfollow_organization(
    session: AsyncSession,
    organization_id: uuid.UUID,
    user,
) -> OrganizationResponse:
    """Unfollow an organization as ``user`` (roadmap 2.3).

    Idempotent: a caller who does not follow the
    organization gets a successful response with the
    (unchanged) follower count rather than an error, so
    retries are safe. An unknown organization id is a 404
    for consistency with the follow endpoint.

    Args:
        session: Active async database session.
        organization_id: Organization to unfollow.
        user: The signed-in account unfollowing.

    Returns:
        The organization payload with its refreshed
        ``followers_count``.

    Raises:
        OrganizationNotFoundError: 404 if no organization
            has this id.
    """
    organization = (
        await session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if organization is None:
        raise OrganizationNotFoundError(str(organization_id))

    existing = await find_follow(session, user.id, organization_id)
    if existing is not None:
        await session.delete(existing)
        await session.commit()
        logger.info(
            "organization.unfollowed",
            organization_id=str(organization_id),
            user_id=str(user.id),
        )
    return _organization_response(
        organization, await count_followers(session, organization_id)
    )
