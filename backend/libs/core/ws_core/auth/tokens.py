"""Self-issued JWT bearer tokens and the httpOnly session cookie.

Token format
------------

:func:`create_access_token` builds a minimal HS256 JWT::

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

Token resolution
----------------

:func:`_resolve_token` prefers the ``Authorization`` header over
the cookie when both are present, which is the only way the test
suite can simulate two users against a single shared client.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

import structlog
from fastapi import HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ws_core.config import get_settings

logger = structlog.get_logger(__name__)

# auto_error=False so cookie-only requests don't 401 before the resolver runs.
_bearer = HTTPBearer(auto_error=False)


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

    Args:
        request: The active request, read for the session cookie.
        credentials: Parsed ``Authorization: Bearer ...`` header,
            or ``None`` when the header is absent.

    Returns:
        The bare token string, or ``None`` when neither source
        supplied one.
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

    Args:
        token: The bare JWT (no ``Bearer`` prefix).

    Returns:
        The ``User.id`` encoded in the ``sub`` claim.

    Raises:
        _InvalidTokenError: With a machine-readable ``reason`` when
            the token is malformed, the signature does not verify,
            the payload is not valid JSON, the ``exp`` has passed,
            or the ``sub`` is not a UUID.
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

    Args:
        exc: The internal validation failure.

    Returns:
        An ``HTTPException`` carrying the generic 401 detail.
    """
    logger.info("auth.rejected_token", reason=exc.reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
    )
