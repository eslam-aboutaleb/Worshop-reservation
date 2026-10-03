"""Organizer-platform auth gate.

The account table, password hashing, JWT issuance, the
session cookie, and the ``get_current_user`` /
``get_current_admin`` / ``get_optional_user``
dependencies are domain-agnostic and live in
:mod:`ws_core.auth`. This module adds the one
domain-specific dependency:

:func:`get_current_organizer` is the organizer-platform
counterpart to ``get_current_admin`` (roadmap 2.1). It
admits the super-admin, accounts with the
``organizer``/``admin`` platform role, and members of
the organization that owns the target workshop. That
rule reads the domain models ``OrganizationMembership``
and ``Workshop``, which belong to this plugin.
"""

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import (
    _ARGON2_MEMORY_COST_KIB,
    _ARGON2_PARALLELISM,
    _ARGON2_TIME_COST,
    _PASSWORD_HASHER,
    DUMMY_PASSWORD_HASH,
    _b64url_decode,
    _b64url_encode,
    _decode_token,
    _InvalidTokenError,
    _validate_token,
    clear_session_cookie,
    create_access_token,
    get_current_admin,
    get_current_user,
    get_optional_user,
    hash_password,
    is_admin,
    set_session_cookie,
    verify_password,
)
from ws_core.auth.dependencies import get_current_user as _core_get_current_user
from ws_core.auth.models import (
    USER_ROLE_ADMIN,
    USER_ROLE_ATTENDEE,
    USER_ROLE_ORGANIZER,
    User,
)
from ws_core.config import get_settings
from ws_core.db.engine import get_db
from ws_core.errors import WorkshopNotFoundError

from ws_reservation.models.organization import OrganizationMembership
from ws_reservation.models.workshop import Workshop

__all__ = [
    "_ARGON2_MEMORY_COST_KIB",
    "_ARGON2_PARALLELISM",
    "_ARGON2_TIME_COST",
    "_PASSWORD_HASHER",
    "_InvalidTokenError",
    "_b64url_decode",
    "_b64url_encode",
    "_decode_token",
    "_validate_token",
    "DUMMY_PASSWORD_HASH",
    "USER_ROLE_ADMIN",
    "USER_ROLE_ATTENDEE",
    "USER_ROLE_ORGANIZER",
    "User",
    "clear_session_cookie",
    "create_access_token",
    "get_current_admin",
    "get_current_organizer",
    "get_current_user",
    "get_optional_user",
    "get_settings",
    "hash_password",
    "is_admin",
    "set_session_cookie",
    "verify_password",
]


async def get_current_organizer(
    request: Request,
    user: User = Depends(_core_get_current_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller and yield them only when they may manage workshops.

    This is the organizer-platform counterpart to
    :func:`ws_core.auth.dependencies.get_current_admin` (roadmap 2.1).
    A caller is an organizer - and therefore allowed through to the
    workshop mutation routes - when any of the following holds:

    * they are the environment-configured super-admin
      (``is_admin``), or
    * their platform ``role`` is ``admin``, or
    * they hold an ``owner``/``member`` membership in
      the organization that owns the target workshop.

    The platform ``organizer`` role is deliberately NOT
    admitted here on its own: creating an organization
    promotes the creator to ``organizer``, so a role-only
    short-circuit would let any registered account manage
    every workshop on the platform after a single
    organization creation. Organizer-role callers - like
    attendee-role callers - must hold a membership in the
    workshop's owning organization; the membership, not the
    role, is the authorization boundary.

    The target workshop is read from the ``workshop_id`` path
    parameter, so the same dependency serves the
    ``PUT /{workshop_id}``, ``POST /{workshop_id}/publish``,
    ``POST /{workshop_id}/cancel`` and ``DELETE /{workshop_id}``
    routes. The ``POST /workshops`` (create) route has no path
    parameter; there any organization membership qualifies the
    caller as an organizer, and the owning organization named in
    the request body is verified by the service layer
    (``workshop_service.create_workshop``).

    Error philosophy: a caller who is not an organizer at all
    (a plain attendee with no organization membership) is
    rejected with 403, preserving the pre-organizer behavior of
    the admin-gated routes. A caller who *is* an organizer but
    may not see the target workshop - because it does not exist,
    is not attached to an organization, or belongs to an
    organization they do not belong to - gets a 404
    (``WorkshopNotFoundError``) so the existence of foreign
    workshops is not leaked.

    Args:
        request: The active request, used to read the
            ``workshop_id`` path parameter.
        user: The signed-in account, resolved by
            :func:`ws_core.auth.dependencies.get_current_user`.
        session: Active async database session (the same
            instance ``get_current_user`` used, since FastAPI
            caches the ``get_db`` dependency per request).

    Returns:
        The resolved ``User`` row.

    Raises:
        HTTPException: 401 if the caller is not signed in
            (raised by ``get_current_user``); 403 if the
            caller is a plain attendee with no organization
            membership, or the target workshop id is
            malformed or unknown.
        WorkshopNotFoundError: 404 if the target workshop
            exists but the caller may not manage it.
    """
    if is_admin(user) or user.role == USER_ROLE_ADMIN:
        return user

    workshop_id = request.path_params.get("workshop_id")
    if workshop_id is None:
        # Create route: any organization membership makes the
        # caller an organizer; the specific organization named
        # in the body is checked by the service layer.
        membership = (
            await session.execute(
                select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organizer access required",
            )
        return user

    try:
        target_id = uuid.UUID(str(workshop_id))
    except (ValueError, TypeError):
        # The route's own ``Path()`` validation would reject
        # this with 422 once the dependency passes; a caller
        # who is not an organizer never gets that far.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organizer access required",
        ) from None

    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == target_id))
    ).scalar_one_or_none()
    if workshop is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organizer access required",
        )
    if workshop.organization_id is None:
        # Platform-managed workshops are admin-only; report
        # them as not found to non-admin organizers.
        raise WorkshopNotFoundError(str(target_id))
    membership = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == workshop.organization_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise WorkshopNotFoundError(str(target_id))
    return user
