"""Account registration and authentication endpoints.

Routes:

* ``POST /auth/signup``  - create a personal account.
* ``POST /auth/login``   - issue a bearer token.
* ``GET  /auth/me``      - current account.
* ``POST /auth/logout``  - clear the cookie.

The bearer token is delivered two ways: as an
httpOnly ``Set-Cookie`` (so the browser SPA works
without touching ``localStorage``) and in the JSON
body (so API clients can choose their own storage).
The frontend sends the token back as
``Authorization: Bearer ...``; the cookie is a
fallback. When both are present the header wins -
this is what lets the test suite simulate two
different users on one client.

Rate limiting (plan 0.3)
--------------------------

``signup`` and ``login`` are protected by a fixed
window keyed on ``(client IP, email)``. The budget
is checked **before** credential verification so a
locked-out caller never reaches the password check,
a failure is recorded on every rejected attempt, and
the budget is cleared on success so a legitimate
user never accumulates a lockout. The counter lives
behind the ``RateLimiter`` interface so Phase 3 can
swap the in-process store for Redis.

Password operations
-------------------

Argon2id hashing and verification run in a worker
thread via ``run_in_threadpool`` so the (deliberately
expensive) KDF never blocks the event loop. The
unknown-email login path verifies against a dummy
hash so both outcomes cost the same, which keeps the
response time from leaking which emails are
registered.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ws_core.auth.dependencies import get_current_user, is_admin
from ws_core.auth.models import User
from ws_core.auth.password import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from ws_core.auth.schemas import (
    AccountCreate,
    AuthResponse,
    LoginRequest,
    UserResponse,
)
from ws_core.auth.tokens import (
    clear_session_cookie,
    create_access_token,
    set_session_cookie,
)
from ws_core.config import get_settings
from ws_core.db.engine import get_db
from ws_core.errors import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    RateLimitedError,
)
from ws_core.rate_limit import auth_rate_limit_key, get_rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Return the peer address, or a placeholder when absent.

    The ASGI test transport does not always populate
    ``request.client``; a missing peer falls back to a
    stable placeholder so the rate-limit key is still
    well-formed.
    """
    return request.client.host if request.client else "unknown"


def _auth_response(user: User) -> AuthResponse:
    """Build the common token-and-user response for auth endpoints."""
    return AuthResponse(
        access_token=create_access_token(user.id),
        user=UserResponse.model_validate(user).model_copy(update={"is_admin": is_admin(user)}),
    )


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    request: Request,
    response: Response,
    payload: AccountCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Create an account and return a freshly-issued bearer token.

    Sets the ``workshop_access_token`` httpOnly cookie on the
    response so the browser session is established without any
    JS-side token handling.

    Args:
        request: Inbound request (used for the rate-limit key).
        response: Active response, mutated to carry the session cookie.
        payload: Validated signup body (full name, email, password).
        session: Active async database session.

    Returns:
        A bearer token and the public view of the new account.

    Raises:
        RateLimitedError: 429 if the ``(IP, email)`` key has
            exceeded its attempt budget.
        EmailAlreadyExistsError: 409 if the email is already
            registered.
    """
    limiter = get_rate_limiter()
    key = auth_rate_limit_key(_client_ip(request), payload.email)
    if not await limiter.check(key):
        raise RateLimitedError(_lockout_seconds())

    existing = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing is not None:
        await limiter.record_failure(key)
        raise EmailAlreadyExistsError(payload.email)

    password_hash = await run_in_threadpool(hash_password, payload.password)
    user = User(
        full_name=payload.full_name.strip(),
        email=payload.email,
        password_hash=password_hash,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Two concurrent signups with the same email: the unique
        # index on `users.email` caught the race. Roll back and
        # return the same 409 the pre-check would have.
        await session.rollback()
        await limiter.record_failure(key)
        raise EmailAlreadyExistsError(payload.email) from exc
    await session.refresh(user)
    await limiter.reset(key)
    auth_response = _auth_response(user)
    set_session_cookie(response, auth_response.access_token)
    return auth_response


@router.post("/login", response_model=AuthResponse)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Sign in with email and password.

    Sets the ``workshop_access_token`` httpOnly cookie on the
    response.

    Args:
        request: Inbound request (used for the rate-limit key).
        response: Active response, mutated to carry the session cookie.
        payload: Validated login body (email, password).
        session: Active async database session.

    Returns:
        A bearer token and the public view of the account.

    Raises:
        RateLimitedError: 429 if the ``(IP, email)`` key has
            exceeded its attempt budget.
        InvalidCredentialsError: 401 if the email is unknown or
            the password does not match. The error message is
            intentionally the same in both cases to avoid
            leaking which emails are registered.
    """
    limiter = get_rate_limiter()
    key = auth_rate_limit_key(_client_ip(request), payload.email)
    if not await limiter.check(key):
        raise RateLimitedError(_lockout_seconds())

    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = await run_in_threadpool(verify_password, payload.password, stored_hash)
    if user is None or not password_matches:
        await limiter.record_failure(key)
        raise InvalidCredentialsError()

    await limiter.reset(key)
    auth_response = _auth_response(user)
    set_session_cookie(response, auth_response.access_token)
    return auth_response


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    """Return the currently signed-in account.

    Useful for the frontend to refresh its cached user view after
    a page reload. The session is now httpOnly-only on the browser
    side, so this endpoint is the only way to rehydrate the
    in-memory user view after the cookie has been written.

    Args:
        user: The signed-in account, resolved by the auth dependency.

    Returns:
        The public view of the account.
    """
    return UserResponse.model_validate(user).model_copy(update={"is_admin": is_admin(user)})


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    """Clear the session cookie. Idempotent - safe to call when signed out.

    The bearer token is stateless, so logout only clears the
    cookie; clients storing the token in ``localStorage`` must
    discard it themselves. The handler mutates the
    FastAPI-injected ``response`` and returns the same instance
    so the cookie header is preserved on the wire.

    Args:
        response: Active response, mutated to clear the cookie.

    Returns:
        The same response with a 204 status.
    """
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _lockout_seconds() -> int:
    """Return the configured lockout window for 429 responses."""
    return get_settings().auth_lockout_seconds
