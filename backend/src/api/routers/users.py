"""Account registration and authentication endpoints.

All routes here are mounted at ``/auth`` by the router's prefix.
The shape of each handler is the same: validate the body, look up
or create the account, return an ``AuthResponse`` containing the
new bearer token (also set as an httpOnly cookie for browsers).

Security notes
--------------

* The bearer token is a self-issued JWT (see :mod:`src.auth`).
* The token is delivered as the ``workshop_access_token`` cookie
  with ``HttpOnly`` and ``SameSite=Lax``; the JSON response still
  carries ``access_token`` so API clients and the test suite can
  use ``Authorization: Bearer ...``.
* ``signup`` is a transactional creation. The email is pre-checked
  to give a clean 409 even though the unique index would also catch
  the race; the pre-check produces a friendlier log entry.
* ``login`` is intentionally permissive about which field is wrong;
  the response message is the same in both cases so an attacker
  cannot enumerate which emails are registered.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import (
    DUMMY_PASSWORD_HASH,
    clear_session_cookie,
    create_access_token,
    get_current_user,
    hash_password,
    is_admin,
    set_session_cookie,
    verify_password,
)
from src.configuration.database import get_db
from src.models.user import User
from src.schemas.auth import AccountCreate, AuthResponse, LoginRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_response(user: User) -> AuthResponse:
    """Build the common token-and-user response for auth endpoints."""
    return AuthResponse(
        access_token=create_access_token(user.id),
        user=UserResponse.model_validate(user).model_copy(update={"is_admin": is_admin(user)}),
    )


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: AccountCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Create an account and return a freshly-issued bearer token.

    Sets the ``workshop_access_token`` httpOnly cookie on the
    response so the browser session is established without any
    JS-side token handling.

    Args:
        payload: Validated signup body (full name, email, password).
        response: Active response, mutated to carry the session cookie.
        session: Active async database session.

    Returns:
        A bearer token and the public view of the new account.

    Raises:
        HTTPException: 409 if the email is already registered.
    """
    existing = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

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
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        ) from exc
    await session.refresh(user)
    auth_response = _auth_response(user)
    set_session_cookie(response, auth_response.access_token)
    return auth_response


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    """Sign in with email and password.

    Sets the ``workshop_access_token`` httpOnly cookie on the
    response.

    Args:
        payload: Validated login body (email, password).
        response: Active response, mutated to carry the session cookie.
        session: Active async database session.

    Returns:
        A bearer token and the public view of the account.

    Raises:
        HTTPException: 401 if the email is unknown or the password
            does not match. The error message is intentionally the
            same in both cases to avoid leaking which emails are
            registered.
    """
    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = await run_in_threadpool(verify_password, payload.password, stored_hash)
    if user is None or not password_matches:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    auth_response = _auth_response(user)
    set_session_cookie(response, auth_response.access_token)
    return auth_response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    """Clear the session cookie. Idempotent - safe to call when signed out.

    The handler mutates the FastAPI-injected ``response`` and
    returns the same instance so the cookie header is preserved
    on the wire. Returning a fresh ``Response(status_code=204)``
    would discard the mutation and the cookie would not be
    cleared in the browser.
    """
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


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
