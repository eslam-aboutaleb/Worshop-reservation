"""FastAPI dependencies that resolve the caller from a bearer token.

``get_current_user`` and ``get_optional_user`` are FastAPI
dependencies. They intentionally use ``Depends(_bearer)`` and
``Depends(get_db)`` directly in the parameter list. This is the
canonical FastAPI idiom and ruff's B008 warning is suppressed at
the rule level (see ``pyproject.toml``).

``get_current_admin`` gates the environment-configured super-admin.
The organizer-platform counterpart (``get_current_organizer``) is
**not** in core: it admits members of the organization that owns
the target workshop, which is a domain rule owned by the
application (and, from plan 03 on, the organizer plugin). It lives
in ``src/auth.py`` and depends on :func:`get_current_user` here.
"""


from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ws_core.auth.models import User
from ws_core.auth.tokens import (
    _bearer,
    _decode_token,
    _resolve_token,
    clear_session_cookie,
)
from ws_core.config import get_settings
from ws_core.db.engine import get_db


def is_admin(user: User) -> bool:
    """Return whether the account matches the configured admin email."""
    admin_email = get_settings().admin_email
    return admin_email is not None and user.email == str(admin_email).lower()


async def get_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the account represented by a bearer token; 401 if missing.

    Use this dependency on endpoints that **require** an authenticated
    user. If the route is "may be anonymous", use
    :func:`get_optional_user` instead.

    Args:
        request: The active request, used to read the session cookie.
        response: The active response, mutated to clear an invalid
            session cookie so a client that has been forcibly signed
            out does not keep retrying the same expired token.
        credentials: Parsed ``Authorization: Bearer ...`` header.
        session: Active async database session.

    Returns:
        The resolved ``User`` row.

    Raises:
        HTTPException: 401 if the header is missing, the token is
            invalid/expired, or the embedded user no longer exists.
    """
    token = _resolve_token(request, credentials)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    try:
        user_id = _decode_token(token)
    except HTTPException:
        clear_session_cookie(response)
        raise
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        clear_session_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Resolve the configured administrator or reject the request with 403."""
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve the account from a bearer token if one is supplied.

    Use this on endpoints that behave differently for anonymous and
    authenticated callers (e.g. the workshop detail view, which
    filters its embedded reservation list to the current user when
    one is signed in.

    Args:
        request: The active request, used to read the session cookie.
        credentials: Parsed ``Authorization: Bearer ...`` header, or
            ``None`` for an anonymous request.
        session: Active async database session.

    Returns:
        The resolved ``User`` row, or ``None`` for anonymous requests
        or when the token is invalid. (Callers that need to
        distinguish "no token" from "bad token" should use
        :func:`get_current_user` instead.)
    """
    token = _resolve_token(request, credentials)
    if token is None:
        return None
    try:
        user_id = _decode_token(token)
    except HTTPException:
        return None
    return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
