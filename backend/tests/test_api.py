"""End-to-end tests via httpx ASGI transport."""

import uuid

from httpx import AsyncClient

from tests.conftest import api_signup, auth_headers


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_create_and_cancel_reservation(client: AsyncClient, workshop_id: str) -> None:
    token, email = await api_signup(client)
    headers = auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())})
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Alice", "attendee_email": "Alice@Example.com"},
        headers=headers,
    )
    assert create.status_code == 201
    body = create.json()
    # The account's email wins over the body's casing/identity.
    assert body["attendee_email"] == email

    replay = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Alice", "attendee_email": "Alice@Example.com"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]

    delete = await client.delete(
        f"/api/reservations/{body['id']}",
        headers=auth_headers(token),
    )
    assert delete.status_code == 200

    # Idempotent cancel
    delete_again = await client.delete(
        f"/api/reservations/{body['id']}",
        headers=auth_headers(token),
    )
    assert delete_again.status_code == 200


async def test_create_requires_authentication(client: AsyncClient, workshop_id: str) -> None:
    """An anonymous create is rejected with 401 and writes no row.

    Anonymous creates used to be allowed, producing reservations with
    ``user_id = NULL``. Nothing could later prove ownership of those
    rows, and the cancel path used to treat "unowned" as "cancellable
    by any signed-in account".
    """
    client.cookies.clear()
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Anon", "attendee_email": f"anon_{uuid.uuid4()}@example.com"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 401


async def test_anonymous_cancel_returns_401(client: AsyncClient, workshop_id: str) -> None:
    """Anonymous cancel of any reservation is rejected with 401."""
    token, email = await api_signup(client)
    create = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Bob", "attendee_email": email},
        headers=auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert create.status_code == 201
    client.cookies.clear()
    response = await client.delete(f"/api/reservations/{create.json()['id']}")
    assert response.status_code == 401


async def test_workshop_full_returns_409(client: AsyncClient, workshop_id: str) -> None:
    for i in range(3):
        token, _ = await api_signup(client)
        resp = await client.post(
            f"/api/workshops/{workshop_id}/reservations",
            json={"attendee_name": f"User {i}", "attendee_email": f"u{i}@example.com"},
            headers=auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert resp.status_code == 201
    token, _ = await api_signup(client)
    full = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Late", "attendee_email": "late@example.com"},
        headers=auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "workshop_full"


async def test_workshop_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/api/workshops/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workshop_not_found"


async def test_missing_idempotency_key_rejected(client: AsyncClient, workshop_id: str) -> None:
    token, email = await api_signup(client)
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Bob", "attendee_email": email},
        headers=auth_headers(token),
    )
    assert response.status_code == 422
