"""Coverage for the organizer dashboard (roadmap 2.2).

Exercises ``GET /api/organizer/stats``:

* total bookings, upcoming session count and
  per-workshop booking counts are computed over
  the caller's own workshops,
* the per-workshop attendee list carries booking
  codes and includes cancelled reservations,
* a plain attendee is rejected with 403,
* organization members see their organization's
  workshops, and the configured super-admin sees
  every workshop (they may belong to no
  organization).
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

ADMIN_EMAIL = "eslamehababoutaleb@gmail.com"
PASSWORD = "Password123!"

# Ids created by the running test, removed again by the
# autouse ``cleanup`` fixture so the shared database is
# not polluted across runs.
_CREATED_ORGANIZATIONS: list[str] = []
_CREATED_WORKSHOPS: list[str] = []


async def _signup(client: AsyncClient, email: str) -> str:
    """Register (or log in) an account and return its bearer token."""
    response = await client.post(
        "/api/auth/signup",
        json={"full_name": "Dashboard Test", "email": email, "password": PASSWORD},
    )
    if response.status_code == 201:
        return response.json()["access_token"]
    assert response.status_code == 409, response.text
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_organization(client: AsyncClient, token: str, name: str) -> dict:
    """Create an organization through the API and track it for cleanup."""
    response = await client.post(
        "/api/organizations", json={"name": name}, headers=_auth(token)
    )
    assert response.status_code == 201, response.text
    organization = response.json()
    _CREATED_ORGANIZATIONS.append(str(organization["id"]))
    return organization


async def _make_workshop(
    client: AsyncClient,
    token: str,
    organization_id: str | None = None,
    title: str = "Dashboard test session",
) -> dict:
    """Create a workshop through the API and track it for cleanup."""
    payload = {
        "title": title,
        "starts_at": "2030-01-15T12:30:00+00:00",
        "max_capacity": 4,
    }
    if organization_id is not None:
        payload["organization_id"] = organization_id
    response = await client.post("/api/workshops", json=payload, headers=_auth(token))
    assert response.status_code == 201, response.text
    workshop = response.json()
    _CREATED_WORKSHOPS.append(str(workshop["id"]))
    return workshop


async def _reserve(
    client: AsyncClient, token: str, workshop_id: str
) -> dict:
    """Reserve a seat on a workshop as the signed-in account."""
    response = await client.post(
        f"/api/workshops/{workshop_id}/reservations",
        json={"attendee_name": "Attendee", "attendee_email": "attendee@example.com"},
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _cancel(client: AsyncClient, token: str, reservation_id: str) -> None:
    """Cancel a reservation owned by the signed-in account."""
    response = await client.delete(
        f"/api/reservations/{reservation_id}", headers=_auth(token)
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
async def cleanup_dashboard(session_factory):
    """Remove workshops and organizations created by the running test."""
    yield
    async with session_factory() as s:
        for workshop_id in _CREATED_WORKSHOPS:
            await s.execute(
                text("DELETE FROM workshops WHERE id = :id"), {"id": workshop_id}
            )
        for organization_id in _CREATED_ORGANIZATIONS:
            await s.execute(
                text("DELETE FROM organizations WHERE id = :id"),
                {"id": organization_id},
            )
        await s.commit()
    _CREATED_WORKSHOPS.clear()
    _CREATED_ORGANIZATIONS.clear()


@pytest.mark.asyncio
async def test_dashboard_reports_bookings_and_attendees(
    client: AsyncClient,
) -> None:
    """Stats, per-workshop counts and the attendee list are correct."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Dashboard Org")
    workshop = await _make_workshop(
        client, owner_token, organization["id"], "Dashboard session"
    )

    attendee_a = await _signup(client, f"attendee_a_{uuid.uuid4()}@example.com")
    attendee_b = await _signup(client, f"attendee_b_{uuid.uuid4()}@example.com")
    reservation_a = await _reserve(client, attendee_a, workshop["id"])
    reservation_b = await _reserve(client, attendee_b, workshop["id"])
    # One attendee releases their seat: the booking count drops
    # but the attendee list keeps the cancelled row.
    await _cancel(client, attendee_a, reservation_a["id"])

    stats = await client.get("/api/organizer/stats", headers=_auth(owner_token))
    assert stats.status_code == 200, stats.text
    payload = stats.json()

    assert payload["total_bookings"] == 1
    assert payload["upcoming_sessions"] == 1
    assert len(payload["workshops"]) == 1

    breakdown = payload["workshops"][0]
    assert breakdown["workshop_id"] == workshop["id"]
    assert breakdown["title"] == "Dashboard session"
    assert breakdown["booking_count"] == 1

    attendees = breakdown["attendees"]
    assert len(attendees) == 2
    by_reservation = {a["reservation_id"]: a for a in attendees}
    assert set(by_reservation) == {reservation_a["id"], reservation_b["id"]}
    # Every row carries a booking code and its status.
    for attendee in attendees:
        assert attendee["booking_code"].startswith("WKS-")
        assert attendee["attendee_name"]
        assert attendee["attendee_email"]
    assert by_reservation[reservation_a["id"]]["status"] == "cancelled"
    assert by_reservation[reservation_b["id"]]["status"] == "active"


@pytest.mark.asyncio
async def test_dashboard_counts_only_active_bookings(
    client: AsyncClient,
) -> None:
    """Cancelling every reservation zeroes the booking counts."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Empty Org")
    workshop = await _make_workshop(
        client, owner_token, organization["id"], "Empty session"
    )

    attendee = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    reservation = await _reserve(client, attendee, workshop["id"])
    await _cancel(client, attendee, reservation["id"])

    stats = await client.get("/api/organizer/stats", headers=_auth(owner_token))
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert payload["total_bookings"] == 0
    assert payload["workshops"][0]["booking_count"] == 0


@pytest.mark.asyncio
async def test_plain_attendee_is_rejected(client: AsyncClient) -> None:
    """A caller with no organizer role or membership gets 403."""
    token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    response = await client.get("/api/organizer/stats", headers=_auth(token))
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_dashboard_requires_authentication(client: AsyncClient) -> None:
    """An anonymous caller is rejected with 401."""
    client.cookies.clear()
    response = await client.get("/api/organizer/stats")
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_member_sees_organization_workshops(
    client: AsyncClient, session_factory,
) -> None:
    """A plain member (attendee role) sees the organization's workshops."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Member Org")
    workshop = await _make_workshop(
        client, owner_token, organization["id"], "Member session"
    )

    # There is no add-member endpoint yet, so the membership is
    # provisioned directly. The member keeps the attendee platform
    # role, which proves the dashboard is scoped by membership.
    member_email = f"member_{uuid.uuid4()}@example.com"
    member_token = await _signup(client, member_email)
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO organization_memberships (user_id, organization_id, role) "
                "SELECT u.id, :oid, 'member' FROM users u WHERE u.email = :email"
            ),
            {"oid": organization["id"], "email": member_email},
        )
        await s.commit()

    stats = await client.get("/api/organizer/stats", headers=_auth(member_token))
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert [w["workshop_id"] for w in payload["workshops"]] == [workshop["id"]]


@pytest.mark.asyncio
async def test_admin_sees_all_workshops(
    client: AsyncClient, session_factory,
) -> None:
    """The configured super-admin sees workshops they do not own."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Admin Org")
    workshop = await _make_workshop(
        client, owner_token, organization["id"], "Admin session"
    )

    attendee = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    await _reserve(client, attendee, workshop["id"])

    # The super-admin belongs to no organization; the dashboard
    # still covers every workshop so it stays useful.
    admin_token = await _signup(client, ADMIN_EMAIL)
    stats = await client.get("/api/organizer/stats", headers=_auth(admin_token))
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert any(w["workshop_id"] == workshop["id"] for w in payload["workshops"])
    # Per-workshop counts are exact even though the totals span
    # every workshop in the shared test database.
    breakdown = next(
        w for w in payload["workshops"] if w["workshop_id"] == workshop["id"]
    )
    assert breakdown["booking_count"] == 1
    assert len(breakdown["attendees"]) == 1

    # Clean up the admin account so later runs can re-signup.
    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM users WHERE email = :email"), {"email": ADMIN_EMAIL}
        )
        await s.commit()
