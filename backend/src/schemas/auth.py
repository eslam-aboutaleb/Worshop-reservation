"""Schemas for account registration and authentication.

Auth flow
---------

* ``POST /api/auth/signup``  → returns ``AuthResponse`` (token + user).
* ``POST /api/auth/login``   → same shape, with credentials checked
  against ``users.password_hash``.
* ``GET  /api/auth/me``      → returns ``UserResponse`` for the
  currently authenticated user.

The bearer token is opaque to the frontend: it is a self-issued JWT
(see ``src/auth.py``) and is stored in ``localStorage`` by
``frontend/src/api.ts``. It is not a session token; there is no
revocation list. If a user wants to "log out everywhere" they rotate
``AUTH_SECRET_KEY`` in the deployment (every issued token becomes
invalid in one stroke).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from src.schemas.common import NormalizedEmail


class AccountCreate(BaseModel):
    """Registration payload for a new personal account.

    Attributes:
        full_name: Display name (1..200 chars).
        email: Valid email; lowercased and trimmed before persistence
            so duplicate-account detection is case-insensitive.
        password: Plain-text password (8..128 chars). Hashed with
            Argon2id before being stored; the plaintext is never
            persisted or logged.
    """

    full_name: str = Field(min_length=1, max_length=200)
    email: NormalizedEmail
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Credentials used to sign in on any device.

    Attributes:
        email: Account email; normalized identically to signup.
        password: Plain-text password to verify against the stored hash.
    """

    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    """Public view of an account.

    Deliberately omits ``password_hash`` so this model is safe to
    embed in any response.

    Attributes:
        id: Account UUID.
        full_name: Display name.
        email: Account email (already lowercased at write time).
        created_at: Account creation timestamp.
        role: Platform role - ``attendee``, ``organizer``, or
            ``admin`` (roadmap 2.1).
        is_admin: ``True`` only for the environment-configured
            super-admin account.
    """

    id: uuid.UUID
    full_name: str
    email: EmailStr
    created_at: datetime
    role: str = "attendee"
    is_admin: bool = False

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    """Token + account returned after signup or login.

    Attributes:
        access_token: JWT bearer token. The frontend stores this in
            ``localStorage`` and sends it as ``Authorization: Bearer ...``.
        token_type: Always ``"bearer"`` (kept explicit for forward
            compatibility if refresh tokens are ever introduced).
        user: The account the token represents.
    """

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
