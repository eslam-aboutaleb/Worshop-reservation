"""Tests for the workshop router (list, detail, SSE smoke).

The list and detail endpoints are simple data shapes; the interesting
parts are the privacy rule on detail and the not-found behavior. The
SSE /events route is covered by the lower-level ``realtime`` tests.
"""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_includes_new_workshop(client: AsyncClient, workshop_id: str) -> None:
    """The created workshop appears in the catalogue list."""
    response = await client.get("/api/workshops")
    assert response.status_code == 200
    payload = response.json()["items"]
    ids = {item["id"] for item in payload}
    assert workshop_id in ids
    found = next(item for item in payload if item["id"] == workshop_id)
    assert found["max_capacity"] == 3
    assert found["available_spots"] == 3
    assert "title" in found and "starts_at" in found


@pytest.mark.asyncio
async def test_list_reflects_reservations(client: AsyncClient, workshop_id: str, api_signup, auth_headers) -> None:
    """available_spots in the list view decrements as reservations are made."""
    for i in range(2):
        token, _ = await api_signup()
        create = await client.post(
            f"/api/workshops/{workshop_id}/reservations",
            json={"attendee_name": f"U{i}", "attendee_email": f"u{i}@example.com"},
            headers=auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert create.status_code == 201

    response = await client.get("/api/workshops")
    item = next(w for w in response.json()["items"] if w["id"] == workshop_id)
    assert item["available_spots"] == 1


@pytest.mark.asyncio
async def test_detail_returns_full_payload(client: AsyncClient, workshop_id: str, api_signup, auth_headers) -> None:
    """The detail endpoint returns the workshop, available spots, and reservations."""
    token, _ = await api_signup()
    await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Detail", "attendee_email": "detail@example.com"},
        headers=auth_headers(token, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    client.cookies.clear()
    response = await client.get(f"/api/workshops/{workshop_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == workshop_id
    assert payload["max_capacity"] == 3
    assert payload["available_spots"] == 2
    assert payload["reservations"] == []  # anonymous sees no reservation list


@pytest.mark.asyncio
async def test_detail_signed_in_user_sees_own_reservation(
    client: AsyncClient, workshop_id: str
) -> None:
    """A signed-in user sees their own active reservation in the detail list."""
    email = f"detail_{uuid.uuid4()}@example.com"
    signup = await client.post(
        "/api/auth/signup",
        json={"full_name": "Detail User", "email": email, "password": "Password123!"},
    )
    token = signup.json()["access_token"]

    await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Me", "attendee_email": email},
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )

    response = await client.get(
        f"/api/workshops/{workshop_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    reservations = response.json()["reservations"]
    assert len(reservations) == 1
    assert reservations[0]["attendee_email"] == email


@pytest.mark.asyncio
async def test_detail_signed_in_user_does_not_see_others_reservation(
    client: AsyncClient, workshop_id: str, api_signup, auth_headers
) -> None:
    """A signed-in user must not see another user's reservation in the list."""
    # The other attendee books a seat first.
    other_token, _ = await api_signup(full_name="Other")
    other = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Other", "attendee_email": f"other_{uuid.uuid4()}@example.com"},
        headers=auth_headers(other_token, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    other_id = other.json()["id"]
    # A different signed-in user views the same workshop.
    token, _ = await api_signup(full_name="Me")
    response = await client.get(
        f"/api/workshops/{workshop_id}",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert all(r["id"] != other_id for r in response.json()["reservations"])


@pytest.mark.asyncio
async def test_detail_unknown_workshop_returns_404(client: AsyncClient) -> None:
    """An unknown workshop id returns the standard error envelope."""
    response = await client.get(f"/api/workshops/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workshop_not_found"


@pytest.mark.asyncio
async def test_detail_invalid_uuid_returns_422(client: AsyncClient) -> None:
    """A non-UUID path segment is rejected at the routing layer."""
    response = await client.get("/api/workshops/not-a-uuid")
    assert response.status_code == 422


# The /events SSE endpoint is an infinite generator and is covered by
# the lower-level ``format_sse`` and ``publish`` tests in
# ``test_realtime.py``. An integration test here would either need a
# timing-based cancel (fragile) or a manual generator close (leaks
# state). The route is mounted in the same router as the list and
# detail endpoints above, which is enough to assert registration.
