"""End-to-end tests via httpx ASGI transport."""

import uuid

from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_create_and_cancel_reservation(client: AsyncClient, workshop_id: str) -> None:
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Alice", "attendee_email": "Alice@Example.com"},
        headers=headers,
    )
    assert create.status_code == 201
    body = create.json()
    assert body["attendee_email"] == "alice@example.com"

    replay = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Alice", "attendee_email": "Alice@Example.com"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]

    # Sign up to obtain a token, then cancel the reservation with it.
    signup = await client.post(
        "/api/auth/signup",
        json={
            "full_name": "Alice",
            "email": f"alice_{uuid.uuid4()}@example.com",
            "password": "Password123!",
        },
    )
    assert signup.status_code == 201, signup.text
    token = signup.json()["access_token"]
    delete = await client.delete(
        f"/api/reservations/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete.status_code == 200

    # Idempotent cancel
    delete_again = await client.delete(
        f"/api/reservations/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_again.status_code == 200


async def test_anonymous_cancel_returns_401(client: AsyncClient, workshop_id: str) -> None:
    """Anonymous cancel of any reservation is rejected with 401."""
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Bob", "attendee_email": f"bob_{uuid.uuid4()}@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert create.status_code == 201
    response = await client.delete(f"/api/reservations/{create.json()['id']}")
    assert response.status_code == 401


async def test_workshop_full_returns_409(client: AsyncClient, workshop_id: str) -> None:
    for i in range(3):
        resp = await client.post(
            f"/api/workshops/{workshop_id}/reservations",
            json={"attendee_name": f"User {i}", "attendee_email": f"u{i}@example.com"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 201
    full = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Late", "attendee_email": "late@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "workshop_full"


async def test_workshop_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/api/workshops/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workshop_not_found"


async def test_missing_idempotency_key_rejected(client: AsyncClient, workshop_id: str) -> None:
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Bob", "attendee_email": "bob@example.com"},
    )
    assert response.status_code == 422
