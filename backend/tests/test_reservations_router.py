"""Tests for the reservations router (account history & auth-gated flows).

Complements the broader API tests in ``test_api.py`` by focusing on
``GET /api/reservations/me`` and the signed-in / signed-out behavior
of create + cancel.
"""

import json
import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import api_signup as _signup_and_token
from tests.conftest import auth_headers as _auth


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
    token, _ = await _signup_and_token(client)
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Anon", "attendee_email": "a@example.com"},
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert create.status_code == 201
    reservation_id = create.json()["id"]
    client.cookies.clear()
    cancel = await client.delete(f"/api/reservations/{reservation_id}")
    assert cancel.status_code == 401


@pytest.mark.asyncio
async def test_signed_in_user_cannot_cancel_unowned_legacy_via_api(
    client: AsyncClient, workshop_id: str
) -> None:
    """A signed-in account must not be able to cancel an unowned row.

    This is the reported bug at the HTTP boundary. Create used to be
    open to anonymous callers, so real rows existed with
    ``user_id = NULL``; the cancel path then treated "unowned" as
    "cancellable by any signed-in account", and every reservation id
    was published on the unauthenticated SSE stream. Any visitor could
    therefore cancel other people's seats.

    The row is written straight to the database to represent that
    pre-fix data; the create endpoint no longer produces unowned rows.
    """
    from sqlalchemy import text

    from tests.conftest import _session_factory

    legacy_id = uuid.uuid4()
    async with _session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reservations "
                "(id, workshop_id, user_id, attendee_name, attendee_email, status) "
                "VALUES (:id, :wid, NULL, :name, :email, 'active')"
            ),
            {
                "id": legacy_id,
                "wid": uuid.UUID(workshop_id),
                "name": "Legacy",
                "email": f"legacy_{uuid.uuid4().hex}@example.com",
            },
        )
        await session.commit()

    token, _ = await _signup_and_token(client)
    cancel = await client.delete(f"/api/reservations/{legacy_id}", headers=_auth(token))
    assert cancel.status_code == 404
    assert cancel.json()["error"]["code"] == "reservation_not_found"

    # Confirm the seat was actually released back to the workshop.
    async with _session_factory() as session:
        status_value = (
            await session.execute(
                text("SELECT status FROM reservations WHERE id = :id"), {"id": legacy_id}
            )
        ).scalar()
    assert status_value == "active"


@pytest.mark.asyncio
async def test_idempotency_replay_does_not_disclose_another_accounts_reservation(
    client: AsyncClient, workshop_id: str
) -> None:
    """Replaying another account's key must not return that account's booking.

    The replay response carries attendee name and email, so an
    unscoped key lookup was a PII disclosure to anyone who could
    obtain another caller's key.
    """
    victim_token, victim_email = await _signup_and_token(client, full_name="Victim Alice")
    key = str(uuid.uuid4())
    victim_create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Victim Alice", "attendee_email": victim_email},
        headers={**_auth(victim_token), "Idempotency-Key": key},
    )
    assert victim_create.status_code == 201

    attacker_token, attacker_email = await _signup_and_token(client, full_name="Attacker Bob")
    replay = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Attacker Bob", "attendee_email": attacker_email},
        headers={**_auth(attacker_token), "Idempotency-Key": key},
    )
    # The attacker already holds a seat? No - this is a different
    # account, so the workshop-level duplicate check does not apply and
    # the key does not resolve for them. A fresh reservation is created
    # under their own identity instead.
    assert replay.status_code == 201
    body = replay.json()
    assert body["id"] != victim_create.json()["id"]
    assert body["attendee_email"] == attacker_email
    assert body["attendee_name"] == "Attacker Bob"
    assert victim_email not in json.dumps(body)


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
    token, _ = await _signup_and_token(client)
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers=_auth(token, **{"Idempotency-Key": ""}),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_idempotency_key_is_rejected(client: AsyncClient, workshop_id: str) -> None:
    """An Idempotency-Key over 255 characters is rejected with 422."""
    token, _ = await _signup_and_token(client)
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers=_auth(token, **{"Idempotency-Key": "k" * 256}),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_max_length_idempotency_key_is_accepted(
    client: AsyncClient, workshop_id: str
) -> None:
    """An Idempotency-Key of exactly 255 characters is accepted."""
    token, _ = await _signup_and_token(client)
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "X", "attendee_email": f"x_{uuid.uuid4()}@example.com"},
        headers=_auth(token, **{"Idempotency-Key": "k" * 255}),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_invalid_workshop_id_in_path_is_rejected(client: AsyncClient) -> None:
    """A non-UUID workshop id is rejected with 422."""
    token, _ = await _signup_and_token(client)
    response = await client.post(
        "/api/workshops/not-a-uuid/reservations",
        json={"attendee_name": "X", "attendee_email": "x@example.com"},
        headers=_auth(token, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_with_invalid_uuid_returns_401(client: AsyncClient) -> None:
    """A non-UUID reservation id is rejected. Auth runs first, so 401 wins."""
    response = await client.delete("/api/reservations/not-a-uuid")
    assert response.status_code == 401
