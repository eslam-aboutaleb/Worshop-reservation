"""Authentication core: accounts, passwords, tokens, and the auth router.

ws-core owns everything about the platform's single account table
and its self-issued bearer tokens:

* :mod:`ws_core.auth.models` - the ``User`` ORM model and the
  platform role constants.
* :mod:`ws_core.auth.password` - salted Argon2id hashing.
* :mod:`ws_core.auth.tokens` - HS256 JWT issuance/validation and
  the httpOnly session cookie.
* :mod:`ws_core.auth.dependencies` - the ``get_current_user``,
  ``get_current_admin`` and ``get_optional_user`` FastAPI
  dependencies plus :func:`is_admin`.
* :mod:`ws_core.auth.schemas` - the auth request/response models
  and the shared :data:`NormalizedEmail` type.
* :mod:`ws_core.auth.routers` - the ``/auth`` router (signup,
  login, ``/me``, logout).

The composition root mounts :mod:`ws_core.auth.routers` under
``/api`` so the routes resolve to ``/api/auth/...``. The
organizer-platform gate (``get_current_organizer``) is a domain
rule and stays in the application until the organizer plugin
extracts it.
"""

from ws_core.auth.dependencies import (
    get_current_admin,
    get_current_user,
    get_optional_user,
    is_admin,
)
from ws_core.auth.models import (
    USER_ROLE_ADMIN,
    USER_ROLE_ATTENDEE,
    USER_ROLE_ORGANIZER,
    User,
)
from ws_core.auth.password import (
    _ARGON2_MEMORY_COST_KIB,
    _ARGON2_PARALLELISM,
    _ARGON2_TIME_COST,
    _PASSWORD_HASHER,
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from ws_core.auth.schemas import (
    AccountCreate,
    AuthResponse,
    LoginRequest,
    NormalizedEmail,
    UserResponse,
)
from ws_core.auth.tokens import (
    _b64url_decode,
    _b64url_encode,
    _bearer,
    _decode_token,
    _InvalidTokenError,
    _validate_token,
    clear_session_cookie,
    create_access_token,
    set_session_cookie,
)

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
    "_bearer",
    "AccountCreate",
    "AuthResponse",
    "DUMMY_PASSWORD_HASH",
    "LoginRequest",
    "NormalizedEmail",
    "USER_ROLE_ADMIN",
    "USER_ROLE_ATTENDEE",
    "USER_ROLE_ORGANIZER",
    "User",
    "UserResponse",
    "clear_session_cookie",
    "create_access_token",
    "get_current_admin",
    "get_current_user",
    "get_optional_user",
    "hash_password",
    "is_admin",
    "set_session_cookie",
    "verify_password",
]
