"""Tests for the reservations router (account history & auth-gated flows).

Complements the broader API tests in ``test_api.py`` by focusing on
``GET /api/reservations/me`` and the signed-in / signed-out behavior
of create + cancel.
"""

import uuid

import pytest
from httpx import AsyncClient


async def _signup_and_token(client: AsyncClient, email: str | None = None) -> tuple[str, str]:
    """Create an account and return (token, email)."""
    email = email or f"resv_{uuid.uuid4()}@example.com"
    response = await client.post(
        "/api/auth/signup",
        json={"full_name": "Test", "email": email, "password": "Password123!"},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"], email


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_me_requires_token(client: AsyncClient) -> None:
    """GET /me without a token is rejected with 401."""
    response = await client.get("/api/reservations/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """A bogus token is rejected with 401."""
    response = await client.get(
        "/api/reservations/me", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_empty_for_new_account(client: AsyncClient) -> None:
    """A fresh account has no reservations yet."""
    token, _ = await _signup_and_token(client)
    response = await client.get("/api/reservations/me", headers=_auth(token))
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_me_lists_signed_in_users_reservations(client: AsyncClient, workshop_id: str) -> None:
    """Creating a reservation as a signed-in user shows up in /me."""
    token, email = await _signup_and_token(client)
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Me", "attendee_email": email},
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert create.status_code == 201

    me = await client.get("/api/reservations/me", headers=_auth(token))
    assert me.status_code == 200
    rows = me.json()
    assert len(rows) == 1
    assert rows[0]["workshop_id"] == workshop_id
    assert rows[0]["workshop_title"]
    assert rows[0]["status"] == "active"
    assert rows[0]["attendee_email"] == email


@pytest.mark.asyncio
async def test_me_includes_cancelled_reservations(client: AsyncClient, workshop_id: str) -> None:
    """Cancelled reservations are still visible (with status='cancelled')."""
    token, email = await _signup_and_token(client)
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Me", "attendee_email": email},
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    reservation_id = create.json()["id"]

    await client.delete(f"/api/reservations/{reservation_id}", headers=_auth(token))

    me = await client.get("/api/reservations/me", headers=_auth(token))
    rows = me.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["cancelled_at"] is not None


@pytest.mark.asyncio
async def test_signed_in_cancel_uses_account_authorization(
    client: AsyncClient, workshop_id: str
) -> None:
    """A signed-in user can only cancel their own (or legacy anonymous) reservations."""
    alice_token, alice_email = await _signup_and_token(
        client, email=f"alice_{uuid.uuid4()}@example.com"
    )
    bob_token, _ = await _signup_and_token(client, email=f"bob_{uuid.uuid4()}@example.com")

    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Alice", "attendee_email": alice_email},
        headers={**_auth(alice_token), "Idempotency-Key": str(uuid.uuid4())},
    )
    reservation_id = create.json()["id"]

    # Bob is authenticated but does not own Alice's reservation.
    bob_cancel = await client.delete(
        f"/api/reservations/{reservation_id}", headers=_auth(bob_token)
    )
    assert bob_cancel.status_code == 404
    assert bob_cancel.json()["error"]["code"] == "reservation_not_found"

    # Alice can cancel her own reservation.
    alice_cancel = await client.delete(
        f"/api/reservations/{reservation_id}", headers=_auth(alice_token)
    )
    assert alice_cancel.status_code == 200


@pytest.mark.asyncio
async def test_anonymous_cancel_is_rejected(client: AsyncClient, workshop_id: str) -> None:
    """Anonymous cancellation by id is no longer permitted; the call must 401.

    Historically the API allowed anyone with a reservation id to
    cancel it; that vector is closed and the call now requires a
    bearer token.
    """
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Anon", "attendee_email": f"a_{uuid.uuid4()}@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert create.status_code == 201
    reservation_id = create.json()["id"]
    cancel = await client.delete(f"/api/reservations/{reservation_id}")
    assert cancel.status_code == 401


@pytest.mark.asyncio
async def test_signed_in_user_cancels_anonymous_legacy_via_api(
    client: AsyncClient, workshop_id: str
) -> None:
    """A signed-in user can cancel an anonymous legacy reservation over HTTP."""
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Anon", "attendee_email": f"a_{uuid.uuid4()}@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert create.status_code == 201
    reservation_id = create.json()["id"]
    token, _ = await _signup_and_token(client)
    cancel = await client.delete(f"/api/reservations/{reservation_id}", headers=_auth(token))
    assert cancel.status_code == 200


@pytest.mark.asyncio
async def test_create_with_signed_in_token_uses_account_email(
    client: AsyncClient, workshop_id: str
) -> None:
    """When a token is present, the account's email wins over the body email."""
    token, email = await _signup_and_token(client)
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Body Name", "attendee_email": "body@example.com"},
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201
    assert response.json()["attendee_email"] == email


@pytest.mark.asyncio
async def test_empty_idempotency_key_is_rejected(client: AsyncClient, workshop_id: str) -> None:
    """An empty Idempotency-Key header is rejected with 422."""
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers={"Idempotency-Key": ""},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_idempotency_key_is_rejected(client: AsyncClient, workshop_id: str) -> None:
    """An Idempotency-Key over 255 characters is rejected with 422."""
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers={"Idempotency-Key": "k" * 256},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_max_length_idempotency_key_is_accepted(
    client: AsyncClient, workshop_id: str
) -> None:
    """An Idempotency-Key of exactly 255 characters is accepted."""
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": f"x_{uuid.uuid4()}@example.com"},
        headers={"Idempotency-Key": "k" * 255},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_invalid_workshop_id_in_path_is_rejected(client: AsyncClient) -> None:
    """A non-UUID workshop id is rejected with 422."""
    response = await client.post(
        "/api/workshops/not-a-uuid/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_with_invalid_uuid_returns_401(client: AsyncClient) -> None:
    """A non-UUID reservation id is rejected. Auth runs first, so 401 wins."""
    response = await client.delete("/api/reservations/not-a-uuid")
    assert response.status_code == 401
