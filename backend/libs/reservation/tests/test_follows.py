"""Coverage for organization following (roadmap 2.3).

Exercises the follow/unfollow surface:

* ``POST   /api/organizations/{id}/follow`` and
  ``DELETE /api/organizations/{id}/follow`` are
  idempotent and maintain ``followers_count``,
* a member cannot follow their own organization
  (409 ``cannot_follow_own_organization``),
* unknown organizations are 404-shaped,
* the discovery list's ``following=true`` filter
  restricts results to workshops from followed
  organizations and requires a signed-in caller.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

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
        json={"full_name": "Follow Test", "email": email, "password": PASSWORD},
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
    organization_id: str,
    title: str = "Follow test session",
) -> dict:
    """Create an organization-owned workshop and track it for cleanup."""
    response = await client.post(
        "/api/workshops",
        json={
            "title": title,
            "starts_at": "2030-01-15T12:30:00+00:00",
            "max_capacity": 4,
            "organization_id": organization_id,
        },
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    workshop = response.json()
    _CREATED_WORKSHOPS.append(str(workshop["id"]))
    return workshop


@pytest.fixture(autouse=True)
async def cleanup_follows(session_factory):
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
async def test_follow_and_unfollow_are_idempotent(client: AsyncClient) -> None:
    """Follow/unfollow round-trips maintain the follower count."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Followed Org")

    follower_token = await _signup(client, f"follower_{uuid.uuid4()}@example.com")
    headers = _auth(follower_token)

    # First follow creates the row (201) and bumps the count.
    first = await client.post(
        f"/api/organizations/{organization['id']}/follow", headers=headers
    )
    assert first.status_code == 201, first.text
    assert first.json()["followers_count"] == 1
    assert first.json()["id"] == organization["id"]
    assert first.json()["membership"] is None

    # Re-following is an idempotent replay (200), not an error.
    replay = await client.post(
        f"/api/organizations/{organization['id']}/follow", headers=headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["followers_count"] == 1

    # Unfollowing drops the count back to zero.
    unfollow = await client.delete(
        f"/api/organizations/{organization['id']}/follow", headers=headers
    )
    assert unfollow.status_code == 200, unfollow.text
    assert unfollow.json()["followers_count"] == 0

    # Unfollowing again is a no-op, not an error.
    again = await client.delete(
        f"/api/organizations/{organization['id']}/follow", headers=headers
    )
    assert again.status_code == 200, again.text
    assert again.json()["followers_count"] == 0


@pytest.mark.asyncio
async def test_member_cannot_follow_own_organization(
    client: AsyncClient, session_factory,
) -> None:
    """A member (owner or plain member) gets a 409 following their own org."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Own Org")

    follow = await client.post(
        f"/api/organizations/{organization['id']}/follow",
        headers=_auth(owner_token),
    )
    assert follow.status_code == 409, follow.text
    assert follow.json()["error"]["code"] == "cannot_follow_own_organization"

    # The rejected follow left no row behind.
    async with session_factory() as s:
        count = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM organization_follows "
                    "WHERE organization_id = :oid"
                ),
                {"oid": organization["id"]},
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_follow_unknown_organization_is_404(client: AsyncClient) -> None:
    """Following a nonexistent organization is 404-shaped."""
    token = await _signup(client, f"follower_{uuid.uuid4()}@example.com")
    response = await client.post(
        f"/api/organizations/{uuid.uuid4()}/follow", headers=_auth(token)
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "organization_not_found"


@pytest.mark.asyncio
async def test_follow_endpoints_require_authentication(client: AsyncClient) -> None:
    """Anonymous follow/unfollow attempts are rejected with 401."""
    client.cookies.clear()
    organization_id = str(uuid.uuid4())

    follow = await client.post(f"/api/organizations/{organization_id}/follow")
    assert follow.status_code == 401, follow.text

    unfollow = await client.delete(f"/api/organizations/{organization_id}/follow")
    assert unfollow.status_code == 401, unfollow.text


@pytest.mark.asyncio
async def test_following_filter_lists_only_followed_organizations(
    client: AsyncClient,
) -> None:
    """``following=true`` shows only workshops from followed organizations."""
    owner_a = await _signup(client, f"owner_a_{uuid.uuid4()}@example.com")
    organization_a = await _make_organization(client, owner_a, "Filter Org A")
    workshop_a = await _make_workshop(
        client, owner_a, organization_a["id"], "Org A session"
    )

    owner_b = await _signup(client, f"owner_b_{uuid.uuid4()}@example.com")
    organization_b = await _make_organization(client, owner_b, "Filter Org B")
    workshop_b = await _make_workshop(
        client, owner_b, organization_b["id"], "Org B session"
    )

    follower = await _signup(client, f"follower_{uuid.uuid4()}@example.com")
    follow = await client.post(
        f"/api/organizations/{organization_a['id']}/follow",
        headers=_auth(follower),
    )
    assert follow.status_code == 201, follow.text

    # The follower sees only Org A's workshop.
    listed = await client.get(
        "/api/workshops", params={"following": "true"}, headers=_auth(follower)
    )
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [workshop_a["id"]]

    # Without the filter the follower sees both organizations' workshops.
    everything = await client.get("/api/workshops", headers=_auth(follower))
    assert everything.status_code == 200, everything.text
    ids = {item["id"] for item in everything.json()["items"]}
    assert workshop_a["id"] in ids
    assert workshop_b["id"] in ids

    # Anonymous + following=true is a 401, not an empty page.
    client.cookies.clear()
    anonymous = await client.get("/api/workshops", params={"following": "true"})
    assert anonymous.status_code == 401, anonymous.text


@pytest.mark.asyncio
async def test_unfollow_updates_the_following_filter(client: AsyncClient) -> None:
    """A workshop disappears from the filter once the caller unfollows."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    organization = await _make_organization(client, owner_token, "Unfollow Org")
    workshop = await _make_workshop(
        client, owner_token, organization["id"], "Unfollow session"
    )

    follower = await _signup(client, f"follower_{uuid.uuid4()}@example.com")
    headers = _auth(follower)
    await client.post(f"/api/organizations/{organization['id']}/follow", headers=headers)

    listed = await client.get("/api/workshops", params={"following": "true"}, headers=headers)
    assert [item["id"] for item in listed.json()["items"]] == [workshop["id"]]

    await client.delete(f"/api/organizations/{organization['id']}/follow", headers=headers)

    listed = await client.get("/api/workshops", params={"following": "true"}, headers=headers)
    assert listed.json()["items"] == []
    assert listed.json()["total"] == 0
