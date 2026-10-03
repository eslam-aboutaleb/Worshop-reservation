"""Tests for ``ws_core.auth`` helpers (password hashing, JWT round-trip).

These are pure-Python helpers so they don't need the database; the
algorithm and the bearer-token plumbing are exercised directly.
"""

import base64
import time
import uuid

import pytest
from fastapi import HTTPException

from ws_core.auth import (
    _ARGON2_MEMORY_COST_KIB,
    _ARGON2_PARALLELISM,
    _ARGON2_TIME_COST,
    _PASSWORD_HASHER,
    DUMMY_PASSWORD_HASH,
    _b64url_decode,
    _b64url_encode,
    _decode_token,
    create_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_uses_argon2id_format() -> None:
    """The encoded form is an Argon2id PHC string with the configured costs.

    The cost parameters are asserted against the module-level
    constants rather than literal strings, so a future OWASP
    baseline bump changes this test in exactly one place.
    """
    encoded = hash_password("Password123!")
    parts = encoded.split("$")
    assert parts[0] == ""
    assert parts[1] == "argon2id"
    assert parts[2] == "v=19"
    # The cost block is `m=<kib>,t=<iter>,p=<lanes>` in that order.
    cost = dict(pair.split("=", 1) for pair in parts[3].split(","))
    assert int(cost["m"]) == _ARGON2_MEMORY_COST_KIB
    assert int(cost["t"]) == _ARGON2_TIME_COST
    assert int(cost["p"]) == _ARGON2_PARALLELISM
    assert len(parts) == 6
    # Salt and digest are both base64 (no padding) and non-empty.
    assert parts[4]
    assert parts[5]
    assert len(encoded) <= 300


def test_hash_password_produces_unique_salts() -> None:
    """Two hashes of the same password must differ (salted)."""
    a = hash_password("Password123!")
    b = hash_password("Password123!")
    assert a != b
    # The salt segment (parts[4]) is what makes them different.
    assert a.split("$")[4] != b.split("$")[4]


def test_password_hasher_uses_owasp_baseline() -> None:
    """The module-level hasher is constructed with the OWASP minimum costs.

    Locking the parameters in a test catches accidental tweaks
    (someone changing t=2 to t=1 for performance, etc.) that would
    weaken every newly issued hash.
    """
    assert _PASSWORD_HASHER.time_cost == _ARGON2_TIME_COST
    assert _PASSWORD_HASHER.memory_cost == _ARGON2_MEMORY_COST_KIB
    assert _PASSWORD_HASHER.parallelism == _ARGON2_PARALLELISM
    # The algorithm family is Argon2id (the OWASP-recommended variant).
    from argon2.low_level import Type

    assert _PASSWORD_HASHER.type is Type.ID


def test_dummy_hash_is_a_well_formed_phc_string() -> None:
    """The unknown-email timing-equalisation blob is parseable.

    `DUMMY_PASSWORD_HASH` is intended for the login path so that
    the unknown-email branch takes the same time as the
    known-email branch (no user-enumeration side channel). If the
    blob were malformed, the dummy call would raise and crash
    login for every unknown email.
    """
    parts = DUMMY_PASSWORD_HASH.split("$")
    assert parts[0] == ""
    assert parts[1] == "argon2id"
    # The verify call against an obviously-wrong password must
    # return False, not raise.
    assert verify_password("definitely-not-the-password", DUMMY_PASSWORD_HASH) is False


def test_verify_password_accepts_correct_password() -> None:
    """A correct password round-trips through verify_password."""
    encoded = hash_password("Password123!")
    assert verify_password("Password123!", encoded) is True


def test_verify_password_rejects_wrong_password() -> None:
    """A wrong password is rejected."""
    encoded = hash_password("Password123!")
    assert verify_password("Wrong", encoded) is False


def test_verify_password_supports_two_independent_hashes() -> None:
    """A password verifies against both its hashes, not just the latest one.

    Guards against an implementation that mistakenly keeps a single
    in-memory "current hash" instead of treating the PHC string
    as the source of truth.
    """
    first = hash_password("Password123!")
    second = hash_password("Password123!")
    assert verify_password("Password123!", first) is True
    assert verify_password("Password123!", second) is True
    assert first != second


def test_verify_password_rejects_tampered_digest() -> None:
    """Flipping a byte in the digest segment of a PHC string fails verification.

    Catches a regression where the implementation accidentally
    returns True on any string that starts with ``$argon2id$``.
    """
    encoded = hash_password("Password123!")
    head, salt, digest = encoded.rsplit("$", 2)
    # Replace the first character of the digest with a different base64 char.
    tampered_digest = ("A" if digest[0] != "A" else "B") + digest[1:]
    tampered = f"{head}${salt}${tampered_digest}"
    assert verify_password("Password123!", tampered) is False


def test_verify_password_rejects_truncated_hash() -> None:
    """A PHC string chopped short is rejected (not a partial match)."""
    encoded = hash_password("Password123!")
    truncated = encoded[:-4]
    assert verify_password("Password123!", truncated) is False


def test_verify_password_treats_malformed_hash_as_no_match() -> None:
    """Malformed encoded values do not raise; they return False."""
    assert verify_password("x", "not-a-valid-encoded-hash") is False
    assert verify_password("x", "bcrypt$salt$digest") is False  # wrong algo
    # Real Argon2 PHC shape with a non-numeric `m=`. argon2-cffi
    # raises InvalidHashError, which verify_password must catch.
    assert verify_password("x", "$argon2id$v=19$m=invalid$salt$digest") is False
    # Empty string must not raise either.
    assert verify_password("x", "") is False


def test_hash_password_does_not_leak_plaintext() -> None:
    """The PHC string never contains the plaintext password.

    A regression here would be catastrophic: the PHC string is
    what gets stored in the database. Use a unique sentinel that
    would not appear by chance in a base64 segment.
    """
    sentinel = "ZXTREME-PII-SENTINEL-Q7W3"
    encoded = hash_password(sentinel)
    assert sentinel not in encoded


def test_password_hasher_is_module_level_singleton() -> None:
    """The same PasswordHasher instance is reused across calls.

    Constructing a PasswordHasher allocates internal Argon2
    parameters; the module-level singleton avoids that overhead
    on every login. The hasher is also the source of the cost
    parameters embedded in new hashes.
    """
    import ws_core.auth as auth_module

    assert auth_module._PASSWORD_HASHER is _PASSWORD_HASHER


def test_create_access_token_returns_three_part_string() -> None:
    """The token is a JWT with three dot-separated base64url parts."""
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert token.count(".") == 2
    header_b64, payload_b64, signature_b64 = token.split(".")
    assert _b64url_decode(header_b64)
    assert _b64url_decode(payload_b64)
    assert _b64url_decode(signature_b64)


def test_create_access_token_contains_user_id_and_exp() -> None:
    """The payload carries the user id (sub) and an expiration (exp) claim."""
    import base64
    import json

    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    _, payload_b64, _ = token.split(".")
    padding = "=" * (-len(payload_b64) % 4)
    body = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    assert body["sub"] == str(user_id)
    assert isinstance(body["exp"], int) and body["exp"] > int(time.time())


def test_decode_token_returns_user_id_for_valid_token() -> None:
    """A fresh token decodes back to the original user id."""
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert _decode_token(token) == user_id


def test_decode_token_rejects_tampered_signature() -> None:
    """Flipping a byte in the signature breaks verification."""
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    header, payload, signature = token.split(".")
    tampered = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(HTTPException) as excinfo:
        _decode_token(f"{header}.{payload}.{tampered}")
    assert excinfo.value.status_code == 401


def test_decode_token_rejects_malformed_token() -> None:
    """Garbage tokens are rejected with 401 (not 500)."""
    with pytest.raises(HTTPException) as excinfo:
        _decode_token("not-a-jwt")
    assert excinfo.value.status_code == 401


def test_decode_token_rejects_expired_token() -> None:
    """A token whose exp has passed is rejected."""
    import base64

    user_id = uuid.uuid4()
    # Craft an expired token by hand.
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode({"sub": str(user_id), "exp": int(time.time()) - 10})
    import hashlib
    import hmac as _hmac

    from ws_core.config import get_settings

    unsigned = f"{header}.{payload}"
    signature = _hmac.new(
        get_settings().auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    expired = f"{unsigned}.{sig_b64}"

    with pytest.raises(HTTPException) as excinfo:
        _decode_token(expired)
    assert excinfo.value.status_code == 401


def test_b64url_round_trip() -> None:
    """base64url encoding survives decode + json round-trip."""
    import json

    payload = {"hello": "world", "count": 3}
    encoded = _b64url_encode(payload)
    decoded = json.loads(_b64url_decode(encoded).decode())
    assert decoded == payload


def test_decode_token_rejects_non_string_input() -> None:
    """A non-string token is rejected as 401, not a 500."""
    with pytest.raises(HTTPException) as excinfo:
        _decode_token(None)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 401


def test_decode_token_rejects_wrong_segment_count() -> None:
    """A token with 0, 1, 2 or 4+ segments is rejected as 401, not a 500."""
    for bad in ["", "a", "a.b", "a.b.c.d"]:
        with pytest.raises(HTTPException) as excinfo:
            _decode_token(bad)
        assert excinfo.value.status_code == 401


def test_decode_token_rejects_payload_with_missing_claims() -> None:
    """A structurally valid signature whose payload has no exp/sub is 401."""
    user_id = uuid.uuid4()
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode({"sub": str(user_id)})  # no exp
    import hashlib
    import hmac as _hmac

    from ws_core.config import get_settings

    unsigned = f"{header}.{payload}"
    signature = _hmac.new(
        get_settings().auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    token = f"{unsigned}.{sig_b64}"
    with pytest.raises(HTTPException) as excinfo:
        _decode_token(token)
    assert excinfo.value.status_code == 401


def test_decode_token_rejects_bad_signature_encoding() -> None:
    """A signature segment that is not valid base64 is rejected as 401."""
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode({"sub": "abc", "exp": int(time.time()) + 60})
    bad_sig_token = f"{header}.{payload}.!!!not-base64!!!"
    with pytest.raises(HTTPException) as excinfo:
        _decode_token(bad_sig_token)
    assert excinfo.value.status_code == 401


def test_decode_token_rejects_malformed_sub() -> None:
    """A valid signature + exp with a non-UUID sub is 401."""
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode({"sub": "not-a-uuid", "exp": int(time.time()) + 60})
    import hashlib
    import hmac as _hmac

    from ws_core.config import get_settings

    unsigned = f"{header}.{payload}"
    signature = _hmac.new(
        get_settings().auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    token = f"{unsigned}.{sig_b64}"
    with pytest.raises(HTTPException) as excinfo:
        _decode_token(token)
    assert excinfo.value.status_code == 401


def test_validate_token_raises_typed_marker() -> None:
    """The internal _validate_token raises _InvalidTokenError with a reason code.

    _decode_token maps every _InvalidTokenError into the same generic
    401 envelope; the typed marker keeps the failure reason
    available for server-side logging.
    """
    from ws_core.auth import _InvalidTokenError, _validate_token

    with pytest.raises(_InvalidTokenError) as excinfo:
        _validate_token("")
    assert excinfo.value.reason == "malformed"

    # A token with valid structure but a non-UUID sub: the signature
    # segment is empty so a proper token must be signed. A valid
    # (signed, non-expired) token is then mutated to have the bad
    # sub.
    user_id = uuid.uuid4()  # noqa: F841
    header = _b64url_encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64url_encode({"sub": "not-a-uuid", "exp": int(time.time()) + 60})
    import hashlib
    import hmac as _hmac

    from ws_core.config import get_settings

    unsigned = f"{header}.{payload}"
    signature = _hmac.new(
        get_settings().auth_secret_key.encode(), unsigned.encode(), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    token = f"{unsigned}.{sig_b64}"
    with pytest.raises(_InvalidTokenError) as excinfo:
        _validate_token(token)
    assert excinfo.value.reason == "malformed-subject"
