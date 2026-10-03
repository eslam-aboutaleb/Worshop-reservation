"""Coverage for organizations and role-based workshop authorization.

Exercises the organizer-platform foundation (roadmap 2.1):
organization creation promotes the creator to owner and
organizer, workshop mutations are authorized by organization
membership or admin, and the ``get_current_organizer``
dependency rejects plain attendees.
"""

import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
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
        json={"full_name": "Org Test", "email": email, "password": PASSWORD},
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
    response = await client.post("/api/organizations", json={"name": name}, headers=_auth(token))
    assert response.status_code == 201, response.text
    organization = response.json()
    _CREATED_ORGANIZATIONS.append(str(organization["id"]))
    return organization


async def _make_workshop(
    client: AsyncClient,
    token: str,
    organization_id: str | None = None,
    title: str = "Org test session",
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


@pytest.fixture(autouse=True)
async def cleanup_organizations(session_factory):
    """Remove organizations and workshops created by the running test."""
    yield
    async with session_factory() as s:
        for workshop_id in _CREATED_WORKSHOPS:
            await s.execute(text("DELETE FROM workshops WHERE id = :id"), {"id": workshop_id})
        for organization_id in _CREATED_ORGANIZATIONS:
            await s.execute(
                text("DELETE FROM organizations WHERE id = :id"),
                {"id": organization_id},
            )
        await s.commit()
    _CREATED_WORKSHOPS.clear()
    _CREATED_ORGANIZATIONS.clear()


@pytest.mark.asyncio
async def test_create_organization_promotes_creator_to_owner(
    client: AsyncClient,
    session_factory,
) -> None:
    """The creator becomes the owner and their role is promoted to organizer."""
    email = f"creator_{uuid.uuid4()}@example.com"
    token = await _signup(client, email)

    organization = await _make_organization(client, token, "Creator Org")
    assert organization["name"] == "Creator Org"
    assert organization["slug"] == "creator-org"
    assert organization["membership"] is not None
    assert organization["membership"]["role"] == "owner"

    # The membership row exists with the owner role and points at the creator.
    async with session_factory() as s:
        rows = (
            await s.execute(
                text("SELECT role FROM organization_memberships WHERE organization_id = :oid"),
                {"oid": organization["id"]},
            )
        ).fetchall()
    assert rows == [("owner",)]

    # The creator's platform role is promoted to organizer.
    me = await client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "organizer"


@pytest.mark.asyncio
async def test_create_workshop_attaches_to_organization(
    client: AsyncClient,
) -> None:
    """A member creating a workshop for their organization attaches it."""
    email = f"owner_{uuid.uuid4()}@example.com"
    token = await _signup(client, email)
    organization = await _make_organization(client, token, "Attach Org")

    workshop = await _make_workshop(client, token, organization["id"])
    assert workshop["organization_id"] == organization["id"]


@pytest.mark.asyncio
async def test_non_member_cannot_mutate_foreign_organization_workshop(
    client: AsyncClient,
) -> None:
    """A caller outside the owning organization gets 404, not 403 or 200."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Foreign Org")
    workshop = await _make_workshop(client, owner_token, organization["id"])

    outsider_token = await _signup(client, f"outsider_{uuid.uuid4()}@example.com")
    headers = _auth(outsider_token)

    update = await client.put(
        f"/api/workshops/{workshop['id']}",
        json={"title": "Hijacked"},
        headers=headers,
    )
    assert update.status_code == 404, update.text
    assert update.json()["error"]["code"] == "workshop_not_found"

    publish = await client.post(f"/api/workshops/{workshop['id']}/publish", headers=headers)
    assert publish.status_code == 404, publish.text

    cancel = await client.post(f"/api/workshops/{workshop['id']}/cancel", headers=headers)
    assert cancel.status_code == 404, cancel.text

    delete = await client.delete(f"/api/workshops/{workshop['id']}", headers=headers)
    assert delete.status_code == 404, delete.text


@pytest.mark.asyncio
async def test_organizer_cannot_mutate_foreign_organization_workshop(
    client: AsyncClient,
) -> None:
    """An organizer-role account from one org gets 404 on another org's workshop.

    Creating an organization promotes the creator to the platform
    ``organizer`` role. That role must not grant platform-wide
    workshop management: authorization flows through the membership
    in the workshop's owning organization, so an organizer of one
    organization cannot mutate another organization's workshop.
    """
    owner_a = await _signup(client, f"owner_a_{uuid.uuid4()}@example.com")
    organization_a = await _make_organization(client, owner_a, "Escalation Org A")
    workshop = await _make_workshop(client, owner_a, organization_a["id"])

    # owner_b owns Org B and therefore holds the organizer role,
    # but has no membership in Org A.
    owner_b = await _signup(client, f"owner_b_{uuid.uuid4()}@example.com")
    await _make_organization(client, owner_b, "Escalation Org B")
    headers = _auth(owner_b)

    update = await client.put(
        f"/api/workshops/{workshop['id']}",
        json={"title": "Hijacked"},
        headers=headers,
    )
    assert update.status_code == 404, update.text
    assert update.json()["error"]["code"] == "workshop_not_found"

    publish = await client.post(f"/api/workshops/{workshop['id']}/publish", headers=headers)
    assert publish.status_code == 404, publish.text

    cancel = await client.post(f"/api/workshops/{workshop['id']}/cancel", headers=headers)
    assert cancel.status_code == 404, cancel.text

    delete = await client.delete(f"/api/workshops/{workshop['id']}", headers=headers)
    assert delete.status_code == 404, delete.text


@pytest.mark.asyncio
async def test_organization_member_can_mutate_own_workshop(
    client: AsyncClient,
    session_factory,
) -> None:
    """A plain attendee holding a membership may manage the organization's workshop."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Member Org")
    workshop = await _make_workshop(client, owner_token, organization["id"])

    # There is no add-member endpoint yet, so the membership is
    # provisioned directly. The member keeps the attendee platform
    # role, which proves authorization flows through the membership.
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

    update = await client.put(
        f"/api/workshops/{workshop['id']}",
        json={"title": "Updated by member"},
        headers=_auth(member_token),
    )
    assert update.status_code == 200, update.text
    assert update.json()["title"] == "Updated by member"

    publish = await client.post(
        f"/api/workshops/{workshop['id']}/publish", headers=_auth(member_token)
    )
    assert publish.status_code == 200, publish.text

    delete = await client.delete(f"/api/workshops/{workshop['id']}", headers=_auth(member_token))
    assert delete.status_code == 204, delete.text


@pytest.mark.asyncio
async def test_admin_can_mutate_organization_workshop(client: AsyncClient) -> None:
    """The configured super-admin may manage any workshop, including org-owned ones."""
    admin_token = await _signup(client, ADMIN_EMAIL)
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Admin Org")
    workshop = await _make_workshop(client, owner_token, organization["id"])

    update = await client.put(
        f"/api/workshops/{workshop['id']}",
        json={"title": "Updated by admin"},
        headers=_auth(admin_token),
    )
    assert update.status_code == 200, update.text
    assert update.json()["title"] == "Updated by admin"

    delete = await client.delete(f"/api/workshops/{workshop['id']}", headers=_auth(admin_token))
    assert delete.status_code == 204, delete.text


@pytest.mark.asyncio
async def test_get_current_organizer_rejects_plain_attendee(
    client: AsyncClient,
) -> None:
    """A plain attendee with no membership is rejected by the organizer dependency."""
    token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    headers = _auth(token)

    create = await client.post(
        "/api/workshops",
        json={
            "title": "Forbidden session",
            "starts_at": "2030-01-15T12:30:00+00:00",
            "max_capacity": 4,
        },
        headers=headers,
    )
    assert create.status_code == 403, create.text

    delete = await client.delete(f"/api/workshops/{uuid.uuid4()}", headers=headers)
    assert delete.status_code == 403, delete.text


@pytest.mark.asyncio
async def test_member_cannot_create_workshop_for_foreign_organization(
    client: AsyncClient,
) -> None:
    """A caller may not attach a workshop to an organization they do not belong to."""
    owner_a = await _signup(client, f"owner_a_{uuid.uuid4()}@example.com")
    organization_a = await _make_organization(client, owner_a, "Org A")

    owner_b = await _signup(client, f"owner_b_{uuid.uuid4()}@example.com")
    await _make_organization(client, owner_b, "Org B")

    # owner_b is an organizer (they own Org B) but holds no membership
    # in Org A, so the service layer refuses the foreign organization.
    create = await client.post(
        "/api/workshops",
        json={
            "title": "Foreign session",
            "starts_at": "2030-01-15T12:30:00+00:00",
            "max_capacity": 4,
            "organization_id": organization_a["id"],
        },
        headers=_auth(owner_b),
    )
    assert create.status_code == 404, create.text
    assert create.json()["error"]["code"] == "organization_not_found"


@pytest.mark.asyncio
async def test_is_organization_member_reflects_membership(
    client: AsyncClient,
    session_factory,
) -> None:
    """The membership predicate is true for members only.

    Called directly rather than through the API so the
    predicate's own query is exercised as a unit: the
    creator of an organization is a member of it, an
    unrelated account is not.
    """
    from sqlalchemy import select
    from ws_core.auth.models import User
    from ws_reservation.models.organization import Organization
    from ws_reservation.services.organization_service import (
        is_organization_member,
    )

    owner_email = f"member_owner_{uuid.uuid4()}@example.com"
    stranger_email = f"member_stranger_{uuid.uuid4()}@example.com"
    owner_token = await _signup(client, owner_email)
    await _signup(client, stranger_email)
    organization = await _make_organization(
        client, owner_token, "Membership Org"
    )

    async with session_factory() as s:
        owner = (
            await s.execute(select(User).where(User.email == owner_email))
        ).scalar_one()
        stranger = (
            await s.execute(select(User).where(User.email == stranger_email))
        ).scalar_one()
        org = (
            await s.execute(
                select(Organization).where(
                    Organization.id == organization["id"]
                )
            )
        ).scalar_one()

        assert await is_organization_member(s, owner.id, org.id) is True
        assert await is_organization_member(s, stranger.id, org.id) is False
