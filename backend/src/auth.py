"""Password hashing and self-issued JWT bearer-token helpers.

The service deliberately does not depend on an external identity
provider. There is one binary, one user table, and one signing key.
That is the right trade-off for a single-replica dev deployment;
swap this module for OIDC / Auth0 / Cognito integration before
going to production.

Password storage
----------------

Passwords are hashed with **salted Argon2id**. ``argon2-cffi`` emits a
self-describing PHC string that embeds the algorithm version, cost
parameters, salt, and digest. ``verify_password`` is the only function
that should ever look at a stored hash; the plaintext password is never
logged, never returned, and never persisted.

Token format
------------

``create_access_token`` builds a minimal HS256 JWT:

    base64url(header).base64url(payload).base64url(hmac_sha256_signature)

There is no ``iss`` / ``aud`` claim. The token is only ever
validated by this same service, so a signing key is enough. ``exp``
is mandatory; the bearer is invalid once it passes. There is no
refresh token and no revocation list; "log out everywhere" is done
by rotating ``AUTH_SECRET_KEY``.

Delivery: httpOnly cookie
-------------------------

Tokens are delivered as the ``workshop_access_token`` cookie with
``HttpOnly`` and ``SameSite=Lax`` (or ``Strict``). The ``Secure``
flag is controlled by ``Settings.cookie_secure`` and is **off** in
the local development environment where HTTPS is not available. JS
on the page cannot read the token. A stored-XSS attacker who
manages to inject script cannot exfiltrate the session. The
``Authorization: Bearer ...`` header is still accepted so the API
remains usable for non-browser clients and for the test suite, but
the browser flow only relies on the cookie.

Helper dependencies
-------------------

``get_current_user`` and ``get_optional_user`` are FastAPI
dependencies. They intentionally use ``Depends(_bearer)`` and
``Depends(get_db)`` directly in the parameter list. This is
the canonical FastAPI idiom and ruff's B008 warning is suppressed at
the rule level (see ``pyproject.toml``).

``get_current_admin`` gates the environment-configured
super-admin. ``get_current_organizer`` is the organizer-platform
counterpart (roadmap 2.1): it admits the super-admin, accounts
with the ``organizer``/``admin`` platform role, and members of
the organization that owns the target workshop, and is the
dependency the workshop mutation routes use.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.configuration.database import get_db
from src.configuration.settings import get_settings
from src.exceptions import WorkshopNotFoundError
from src.models.organization import OrganizationMembership
from src.models.user import USER_ROLE_ADMIN, USER_ROLE_ORGANIZER, User
from src.models.workshop import Workshop

logger = structlog.get_logger(__name__)

# auto_error=False so cookie-only requests don't 401 before the resolver runs.
_bearer = HTTPBearer(auto_error=False)

# OWASP's Argon2id baseline: 19 MiB of memory, two iterations, and one lane.
# The PHC string embeds these values, so the stored hash remains self-describing.
_ARGON2_MEMORY_COST_KIB = 19 * 1024
_ARGON2_TIME_COST = 2
_ARGON2_PARALLELISM = 1
_PASSWORD_HASHER = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST_KIB,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A valid hash keeps the unknown-email login path as expensive as
# the known-email path without generating a new hash for every request.
DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("dummy")


def is_admin(user: User) -> bool:
    """Return whether the account matches the configured admin email."""
    admin_email = get_settings().admin_email
    return admin_email is not None and user.email == str(admin_email).lower()


def _resolve_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Return the bearer token from the cookie or the Authorization header.

    The cookie path is the one used by the browser; the header
    path is the one used by API clients and the test suite. When
    both are present (a client explicitly chooses to send both)
    the header wins so callers can override the cookie per
    request, which is the only way the test suite can simulate
    two users against a single shared client.
    """
    if credentials is not None:
        return credentials.credentials
    settings = get_settings()
    return request.cookies.get(settings.cookie_name)


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the bearer token as an httpOnly cookie on ``response``."""
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=60 * 60 * 24 * settings.access_token_expire_days,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the bearer-token cookie. Idempotent; safe to call when signed out."""
    settings = get_settings()
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        httponly=True,
    )


def hash_password(password: str) -> str:
    """Hash a plaintext password with salted Argon2id.

    Args:
        password: The plaintext password to hash. Never logged.

    Returns:
        A self-describing Argon2id PHC string containing the random
        salt, digest, and cost parameters.
    """
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Check a plaintext password against a stored Argon2id hash.

    ``argon2-cffi`` performs the algorithm-specific verification and
    comparison. Malformed hashes and mismatches are treated as a
    non-match so a corrupt database row cannot crash login.

    Args:
        password: Plaintext password from the login request.
        encoded: Value from ``users.password_hash``.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.
    """
    try:
        return _PASSWORD_HASHER.verify(encoded, password)
    except (InvalidHashError, VerificationError):
        return False


def _b64url_encode(value: dict[str, object]) -> str:
    """Encode a dict as a URL-safe base64 string without padding.

    Args:
        value: JSON-serializable payload.

    Returns:
        The base64url-encoded string (no ``=`` padding).
    """
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    """Decode a URL-safe base64 string (with or without padding).

    Args:
        value: base64url string.

    Returns:
        The decoded bytes. Raises ``ValueError`` on invalid input;
        callers should catch broadly.
    """
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(user_id: uuid.UUID) -> str:
    """Create a signed bearer token for ``user_id``.

    Args:
        user_id: Account UUID to embed in the ``sub`` claim.

    Returns:
        A ``header.payload.signature`` string suitable for use as
        ``Authorization: Bearer ...``.
    """
    settings = get_settings()
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode(
        {
            "sub": str(user_id),
            "exp": int(time.time()) + 60 * 60 * 24 * settings.access_token_expire_days,
        }
    )
    unsigned = f"{header}.{payload}"
    signature = hmac.new(
        settings.auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{unsigned}.{encoded_signature}"


class _InvalidTokenError(Exception):
    """Internal marker for any token-validation failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decode_token(token: str) -> uuid.UUID:
    """Validate ``token`` and return the embedded user id.

    Args:
        token: The bare JWT (no ``Bearer`` prefix).

    Returns:
        The ``User.id`` encoded in the ``sub`` claim.

    Raises:
        HTTPException: 401 if the signature is invalid, the token is
            malformed, the ``exp`` claim has passed, or the embedded
            payload cannot be turned into a user id.
    """
    try:
        return _validate_token(token)
    except _InvalidTokenError as exc:
        raise _token_error_response(exc) from exc


def _validate_token(token: str) -> uuid.UUID:
    """Parse and verify a JWT. Raise ``_InvalidTokenError`` on any failure.

    Split out of :func:`_decode_token` so the success and failure
    paths are each testable in isolation, and so the 401 envelope
    is constructed in exactly one place (:func:`_token_error_response`).
    """
    if not isinstance(token, str) or token.count(".") != 2:
        raise _InvalidTokenError("malformed")
    try:
        header, payload, signature = token.split(".")
    except ValueError as exc:  # pragma: no cover: count() guard above
        raise _InvalidTokenError("malformed") from exc

    unsigned = f"{header}.{payload}"
    expected = hmac.new(
        get_settings().auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    try:
        provided = _b64url_decode(signature)
    except (ValueError, TypeError) as exc:
        raise _InvalidTokenError("bad-signature") from exc
    if not hmac.compare_digest(expected, provided):
        raise _InvalidTokenError("bad-signature")

    try:
        body = json.loads(_b64url_decode(payload))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _InvalidTokenError("malformed-payload") from exc

    if not isinstance(body, dict):
        raise _InvalidTokenError("malformed-payload")
    exp = body.get("exp")
    sub = body.get("sub")
    if not isinstance(exp, int) or not isinstance(sub, str):
        raise _InvalidTokenError("malformed-payload")
    if exp < int(time.time()):
        raise _InvalidTokenError("expired")
    try:
        return uuid.UUID(sub)
    except (ValueError, TypeError) as exc:
        raise _InvalidTokenError("malformed-subject") from exc


def _token_error_response(exc: _InvalidTokenError) -> HTTPException:
    """Map an internal token failure to the user-facing 401 envelope.

    A single 401 with a generic message is returned regardless of
    the underlying cause so the endpoint does not leak whether a
    token was structurally invalid, expired, or had a bad
    signature. The specific reason is logged server-side for
    observability but never appears in the response.
    """
    logger.info("auth.rejected_token", reason=exc.reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
    )


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


async def get_current_organizer(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller and yield them only when they may manage workshops.

    This is the organizer-platform counterpart to
    :func:`get_current_admin` (roadmap 2.1). A caller is an
    organizer - and therefore allowed through to the workshop
    mutation routes - when any of the following holds:

    * they are the environment-configured super-admin
      (``is_admin``), or
    * their platform ``role`` is ``organizer`` or ``admin``, or
    * they hold an ``owner``/``member`` membership in the
      organization that owns the target workshop.

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
            :func:`get_current_user`.
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
    if is_admin(user) or user.role in (USER_ROLE_ORGANIZER, USER_ROLE_ADMIN):
        return user

    workshop_id = request.path_params.get("workshop_id")
    if workshop_id is None:
        # Create route: any organization membership makes the
        # caller an organizer; the specific organization named
        # in the body is checked by the service layer.
        membership = (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id
                )
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
        )

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
