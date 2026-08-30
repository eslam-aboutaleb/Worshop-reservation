"""Coverage for environment-configured workshop administration."""

import uuid

import pytest
from httpx import AsyncClient

ADMIN_EMAIL = "eslamehababoutaleb@gmail.com"
PASSWORD = "Password123!"


async def _signup(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/auth/signup",
        json={"full_name": "Workshop Admin", "email": email, "password": PASSWORD},
    )
    if response.status_code == 201:
        return response.json()["access_token"]
    assert response.status_code == 409, response.text
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_configured_admin_can_create_and_cancel_unbooked_workshop(
    client: AsyncClient,
) -> None:
    """Admin-created sessions are visible and may be cancelled until booked."""
    token = await _signup(client, ADMIN_EMAIL)
    payload = {
        "title": "Admin-created session",
        "starts_at": "2030-01-15T12:30:00+00:00",
        "max_capacity": 4,
    }
    create = await client.post("/api/workshops", json=payload, headers=_auth(token))
    assert create.status_code == 201, create.text
    workshop = create.json()
    assert workshop["available_spots"] == 4

    catalogue = await client.get("/api/workshops")
    assert any(item["id"] == workshop["id"] for item in catalogue.json())

    delete = await client.delete(f"/api/workshops/{workshop['id']}", headers=_auth(token))
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_auth_responses_expose_admin_flag(client: AsyncClient) -> None:
    """Only the configured account receives the frontend authorization flag."""
    admin = await client.post(
        "/api/auth/signup",
        json={"full_name": "Workshop Admin", "email": ADMIN_EMAIL, "password": PASSWORD},
    )
    if admin.status_code == 409:
        admin = await client.post(
            "/api/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD}
        )
    assert admin.status_code == 200 or admin.status_code == 201
    assert admin.json()["user"]["is_admin"] is True

    member = await client.post(
        "/api/auth/signup",
        json={
            "full_name": "Member",
            "email": f"member_{uuid.uuid4()}@example.com",
            "password": PASSWORD,
        },
    )
    assert member.status_code == 201
    assert member.json()["user"]["is_admin"] is False


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_workshops(client: AsyncClient) -> None:
    """An ordinary account cannot create or cancel sessions."""
    token = await _signup(client, f"member_{uuid.uuid4()}@example.com")
    create = await client.post(
        "/api/workshops",
        json={
            "title": "Forbidden session",
            "starts_at": "2030-01-15T12:30:00+00:00",
            "max_capacity": 4,
        },
        headers=_auth(token),
    )
    assert create.status_code == 403

    delete = await client.delete(f"/api/workshops/{uuid.uuid4()}", headers=_auth(token))
    assert delete.status_code == 403


@pytest.mark.asyncio
async def test_booked_workshop_cannot_be_cancelled(client: AsyncClient) -> None:
    """Active reservations protect a workshop from administrator cancellation."""
    admin_token = await _signup(client, ADMIN_EMAIL)
    created = await client.post(
        "/api/workshops",
        json={
            "title": "Protected session",
            "starts_at": "2030-02-15T12:30:00+00:00",
            "max_capacity": 1,
        },
        headers=_auth(admin_token),
    )
    workshop_id = created.json()["id"]
    attendee_token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    reserve = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Attendee", "attendee_email": "ignored@example.com"},
        headers={**_auth(attendee_token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert reserve.status_code == 201

    delete = await client.delete(f"/api/workshops/{workshop_id}", headers=_auth(admin_token))
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "workshop_has_active_reservations"

    cancelled = await client.delete(
        f"/api/reservations/{reserve.json()['id']}", headers=_auth(attendee_token)
    )
    assert cancelled.status_code == 200
    assert (
        await client.delete(f"/api/workshops/{workshop_id}", headers=_auth(admin_token))
    ).status_code == 204


@pytest.fixture(autouse=True)
async def cleanup_admin():
    from sqlalchemy import text

    from tests.conftest import _session_factory

    async with _session_factory() as s:
        await s.execute(text("DELETE FROM users WHERE email = :email"), {"email": ADMIN_EMAIL})
        await s.commit()
