"""Tests for the auth router (signup, login, /me).

Covers the happy paths, the 409-on-duplicate-email case, the
no-leakage behavior of login, and the /me dependency.
"""

import uuid
from threading import get_ident

import pytest
from httpx import AsyncClient
from ws_core.auth import routers as users_router

from ws_core.auth import hash_password as real_hash_password
from ws_core.auth import verify_password as real_verify_password


def _signup_body(email: str = "user@example.com", password: str = "Password123!") -> dict:
    return {"full_name": "Test User", "email": email, "password": password}


async def _signup(client: AsyncClient, email: str = "user@example.com") -> dict:
    response = await client.post("/api/auth/signup", json=_signup_body(email=email))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_signup_creates_account_and_returns_token(client: AsyncClient) -> None:
    """Signup returns 201 with a bearer token and a public user view."""
    body = await _signup(client, email=f"new_{uuid.uuid4()}@example.com")
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert body["user"]["email"]
    # password_hash must never appear in the response.
    assert "password_hash" not in body["user"]


@pytest.mark.asyncio
async def test_signup_normalizes_email_to_lowercase(client: AsyncClient) -> None:
    """Mixed-case email is stored lowercased so the unique index catches duplicates."""
    email = f"MiXeD_{uuid.uuid4()}@Example.COM"
    body = await _signup(client, email=email)
    assert body["user"]["email"] == email.lower()


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_409(client: AsyncClient) -> None:
    """A second signup with the same email must fail with 409."""
    email = f"dup_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    second = await client.post("/api/auth/signup", json=_signup_body(email=email))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "email_already_exists"
    assert "already exists" in second.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_signup_rejects_short_password(client: AsyncClient) -> None:
    """Passwords shorter than 8 characters are rejected at the schema layer."""
    response = await client.post(
        "/api/auth/signup",
        json={"full_name": "X", "email": f"a_{uuid.uuid4()}@example.com", "password": "short"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_signup_rejects_invalid_email(client: AsyncClient) -> None:
    """Malformed email addresses fail Pydantic validation."""
    response = await client.post(
        "/api/auth/signup",
        json={"full_name": "X", "email": "not-an-email", "password": "Password123!"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_signup_rejects_empty_full_name(client: AsyncClient) -> None:
    """An empty full_name is rejected with 422."""
    response = await client.post(
        "/api/auth/signup",
        json={
            "full_name": "",
            "email": f"a_{uuid.uuid4()}@example.com",
            "password": "Password123!",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_with_valid_credentials_returns_token(client: AsyncClient) -> None:
    """Login after signup returns a fresh bearer for the same account."""
    email = f"login_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == email


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(client: AsyncClient) -> None:
    """A wrong password must produce a 401, not 404."""
    email = f"pw_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": "WrongPassword!"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_with_unknown_email_returns_401(client: AsyncClient) -> None:
    """An unknown email must also produce 401, not 404 (no enumeration)."""
    response = await client.post(
        "/api/auth/login",
        json={"email": f"ghost_{uuid.uuid4()}@example.com", "password": "Password123!"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_with_unknown_email_still_verifies_dummy_hash(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown-email login still performs password verification work."""
    called = False

    def fake_verify_password(password: str, encoded: str) -> bool:
        nonlocal called
        called = True
        assert password == "Password123!"
        assert encoded == users_router.DUMMY_PASSWORD_HASH
        return False

    monkeypatch.setattr(users_router, "verify_password", fake_verify_password)
    response = await client.post(
        "/api/auth/login",
        json={"email": f"ghost_{uuid.uuid4()}@example.com", "password": "Password123!"},
    )

    assert response.status_code == 401
    assert called is True


@pytest.mark.asyncio
async def test_password_operations_run_off_event_loop(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signup and login execute password operations in worker threads."""
    event_loop_thread = get_ident()
    operation_threads: list[int] = []

    def fake_hash_password(password: str) -> str:
        operation_threads.append(get_ident())
        return real_hash_password(password)

    def fake_verify_password(password: str, encoded: str) -> bool:
        operation_threads.append(get_ident())
        return real_verify_password(password, encoded)

    monkeypatch.setattr(users_router, "hash_password", fake_hash_password)
    monkeypatch.setattr(users_router, "verify_password", fake_verify_password)

    email = f"thread_{uuid.uuid4()}@example.com"
    signup = await client.post(
        "/api/auth/signup",
        json={"full_name": "Thread Test", "email": email, "password": "Password123!"},
    )
    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "Password123!"},
    )

    assert signup.status_code == 201
    assert login.status_code == 200
    assert operation_threads
    assert all(thread_id != event_loop_thread for thread_id in operation_threads)


@pytest.mark.asyncio
async def test_login_error_message_does_not_leak_existence(client: AsyncClient) -> None:
    """Wrong-password and unknown-email must surface the same 401 detail."""
    email = f"leak_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    wrong_pw = await client.post(
        "/api/auth/login", json={"email": email, "password": "WrongPassword!"}
    )
    unknown = await client.post(
        "/api/auth/login",
        json={"email": f"ghost_{uuid.uuid4()}@example.com", "password": "Password123!"},
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


@pytest.mark.asyncio
async def test_login_with_mixed_case_email_succeeds(client: AsyncClient) -> None:
    """Email is case-insensitive at login time too."""
    email = f"case_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    response = await client.post(
        "/api/auth/login",
        json={"email": email.upper(), "password": "Password123!"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_me_returns_signed_in_user(client: AsyncClient) -> None:
    """GET /me with a valid token returns the user's public view."""
    email = f"me_{uuid.uuid4()}@example.com"
    body = await _signup(client, email=email)
    token = body["access_token"]
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == email


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    """Missing Authorization header is a 401 (not a 403)."""
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """A tampered or malformed token is rejected with 401."""
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_two_signups_with_distinct_emails_succeed(client: AsyncClient) -> None:
    """Two unrelated signups both succeed."""
    a = f"a_{uuid.uuid4()}@example.com"
    b = f"b_{uuid.uuid4()}@example.com"
    assert (await client.post("/api/auth/signup", json=_signup_body(email=a))).status_code == 201
    assert (await client.post("/api/auth/signup", json=_signup_body(email=b))).status_code == 201


@pytest.mark.asyncio
async def test_six_rapid_login_attempts_trigger_429_lockout(
    client: AsyncClient,
) -> None:
    """The (IP, email) budget locks out after the configured attempts.

    Five failed attempts are allowed (``auth_max_attempts``);
    the sixth must be refused with 429 and the
    ``rate_limited`` error code before any password
    verification runs. A unique email keeps the autouse
    limiter reset the only thing that could clear the
    budget mid-test.
    """
    email = f"lockout_{uuid.uuid4()}@example.com"
    for _ in range(5):
        failed = await client.post(
            "/api/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        assert failed.status_code == 401
    locked = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "WrongPassword!"},
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_login_sets_session_cookie(client: AsyncClient) -> None:
    """A successful login sets the httpOnly session cookie."""
    email = f"cookie_{uuid.uuid4()}@example.com"
    await _signup(client, email=email)
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200
    assert "workshop_access_token" in response.cookies
    assert response.cookies["workshop_access_token"]


@pytest.mark.asyncio
async def test_me_accepts_session_cookie(client: AsyncClient) -> None:
    """A request with only the session cookie is treated as authenticated."""
    email = f"c_{uuid.uuid4()}@example.com"
    body = await _signup(client, email=email)  # noqa: F841
    # Drop the bearer from the test client so the cookie is the
    # only credential in flight.
    client.headers.pop("Authorization", None)
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == email


@pytest.mark.asyncio
async def test_logout_clears_session_cookie(client: AsyncClient) -> None:
    """POST /api/auth/logout clears the session cookie and returns 204."""
    await _signup(client, email=f"lo_{uuid.uuid4()}@example.com")
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert "workshop_access_token" not in response.cookies
