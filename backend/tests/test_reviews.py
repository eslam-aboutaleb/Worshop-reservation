"""Coverage for workshop reviews (roadmap 2.4).

Exercises ``POST /api/workshops/{id}/reviews`` and
the review surface on the workshop detail payload:

* a caller with no past reservation cannot review
  (409 ``review_not_eligible``),
* a past attendee can review, and the detail view
  shows the aggregate rating and the review list,
* one review per user per workshop
  (409 ``review_already_exists``),
* a cancelled reservation still counts as "held",
* a session that has not ended cannot be reviewed,
* rating bounds are validated at the schema layer
  (422).
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import _session_factory

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
        json={"full_name": "Review Test", "email": email, "password": PASSWORD},
    )
    if response.status_code == 201:
        return response.json()["access_token"]
    assert response.status_code == 409, response.text
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_workshop(
    client: AsyncClient,
    token: str,
    title: str = "Review test session",
) -> dict:
    """Create a future workshop through the API and track it for cleanup.

    Workshop creation requires an organizer, so the caller
    first creates an organization (which promotes them to
    the organizer role) and the workshop is attached to it.
    """
    organization = await client.post(
        "/api/organizations",
        json={"name": f"Review Org {uuid.uuid4().hex[:8]}"},
        headers=_auth(token),
    )
    assert organization.status_code == 201, organization.text
    _CREATED_ORGANIZATIONS.append(str(organization.json()["id"]))

    response = await client.post(
        "/api/workshops",
        json={
            "title": title,
            "starts_at": "2030-01-15T12:30:00+00:00",
            "ends_at": "2030-01-15T14:30:00+00:00",
            "max_capacity": 4,
            "organization_id": organization.json()["id"],
        },
        headers=_auth(token),
    )
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


async def _move_workshop_to_the_past(workshop_id: str) -> None:
    """Simulate time passing for a workshop created by the test."""
    async with _session_factory() as s:
        await s.execute(
            text(
                "UPDATE workshops "
                "SET starts_at = :starts, ends_at = :ends "
                "WHERE id = :id"
            ),
            {
                "starts": datetime(2020, 1, 15, 12, 30, tzinfo=UTC),
                "ends": datetime(2020, 1, 15, 14, 30, tzinfo=UTC),
                "id": workshop_id,
            },
        )
        await s.commit()


@pytest.fixture(autouse=True)
async def cleanup_reviews():
    """Remove workshops and organizations created by the running test."""
    yield
    async with _session_factory() as s:
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
async def test_user_without_reservation_cannot_review(
    client: AsyncClient,
) -> None:
    """A caller who never held a reservation gets a 409."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)
    await _move_workshop_to_the_past(workshop["id"])

    stranger = await _signup(client, f"stranger_{uuid.uuid4()}@example.com")
    response = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 5, "text": "Great session"},
        headers=_auth(stranger),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "review_not_eligible"


@pytest.mark.asyncio
async def test_past_attendee_can_review_and_detail_shows_aggregate(
    client: AsyncClient,
) -> None:
    """A past attendee reviews; the detail view shows the aggregate."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    attendee_token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    await _reserve(client, attendee_token, workshop["id"])

    # The session has not ended yet: not eligible.
    early = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 4},
        headers=_auth(attendee_token),
    )
    assert early.status_code == 409, early.text
    assert early.json()["error"]["code"] == "review_not_eligible"

    await _move_workshop_to_the_past(workshop["id"])

    review = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 4, "text": "Solid session"},
        headers=_auth(attendee_token),
    )
    assert review.status_code == 201, review.text
    payload = review.json()
    assert payload["rating"] == 4
    assert payload["text"] == "Solid session"
    assert payload["workshop_id"] == workshop["id"]

    # The detail payload carries the aggregate and the list.
    detail = await client.get(f"/api/workshops/{workshop['id']}")
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert data["rating_average"] == 4.0
    assert data["rating_count"] == 1
    assert len(data["reviews"]) == 1
    assert data["reviews"][0]["rating"] == 4
    assert data["reviews"][0]["text"] == "Solid session"
    assert data["reviews"][0]["user_name"] == "Review Test"


@pytest.mark.asyncio
async def test_one_review_per_user_per_workshop(
    client: AsyncClient,
) -> None:
    """A second review for the same workshop is rejected."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    attendee_token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    await _reserve(client, attendee_token, workshop["id"])
    await _move_workshop_to_the_past(workshop["id"])

    first = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 5},
        headers=_auth(attendee_token),
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 1},
        headers=_auth(attendee_token),
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "review_already_exists"

    # The original review is unchanged.
    detail = await client.get(f"/api/workshops/{workshop['id']}")
    assert detail.json()["rating_count"] == 1
    assert detail.json()["rating_average"] == 5.0


@pytest.mark.asyncio
async def test_cancelled_reservation_still_counts_as_held(
    client: AsyncClient,
) -> None:
    """A cancelled booking still grants review eligibility."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    attendee_token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    reservation = await _reserve(client, attendee_token, workshop["id"])
    cancel = await client.delete(
        f"/api/reservations/{reservation['id']}", headers=_auth(attendee_token)
    )
    assert cancel.status_code == 200, cancel.text

    await _move_workshop_to_the_past(workshop["id"])

    review = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 3},
        headers=_auth(attendee_token),
    )
    assert review.status_code == 201, review.text


@pytest.mark.asyncio
async def test_rating_bounds_are_validated(client: AsyncClient) -> None:
    """Ratings outside 1..5 are a 422 before any database work."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    attendee_token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    await _reserve(client, attendee_token, workshop["id"])
    await _move_workshop_to_the_past(workshop["id"])

    for rating in (0, 6, -1, 100):
        response = await client.post(
            f"/api/workshops/{workshop['id']}/reviews",
            json={"rating": rating},
            headers=_auth(attendee_token),
        )
        assert response.status_code == 422, response.text

    # No out-of-range review was persisted.
    detail = await client.get(f"/api/workshops/{workshop['id']}")
    assert detail.json()["rating_count"] == 0


@pytest.mark.asyncio
async def test_review_requires_authentication(client: AsyncClient) -> None:
    """An anonymous review attempt is rejected with 401."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    client.cookies.clear()
    response = await client.post(
        f"/api/workshops/{workshop['id']}/reviews",
        json={"rating": 5},
    )
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_review_of_unknown_workshop_is_404(
    client: AsyncClient,
) -> None:
    """Reviewing a nonexistent workshop is 404-shaped."""
    token = await _signup(client, f"attendee_{uuid.uuid4()}@example.com")
    response = await client.post(
        f"/api/workshops/{uuid.uuid4()}/reviews",
        json={"rating": 5},
        headers=_auth(token),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "workshop_not_found"


@pytest.mark.asyncio
async def test_aggregate_averages_multiple_reviews(
    client: AsyncClient,
) -> None:
    """The average rating is computed across reviewers."""
    owner_token = await _signup(client, f"owner_{uuid.uuid4()}@example.com")
    workshop = await _make_workshop(client, owner_token)

    first = await _signup(client, f"first_{uuid.uuid4()}@example.com")
    second = await _signup(client, f"second_{uuid.uuid4()}@example.com")
    await _reserve(client, first, workshop["id"])
    await _reserve(client, second, workshop["id"])
    await _move_workshop_to_the_past(workshop["id"])

    for token, rating in ((first, 5), (second, 3)):
        response = await client.post(
            f"/api/workshops/{workshop['id']}/reviews",
            json={"rating": rating},
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text

    detail = await client.get(f"/api/workshops/{workshop['id']}")
    data = detail.json()
    assert data["rating_count"] == 2
    assert data["rating_average"] == 4.0
    # Newest first.
    assert data["reviews"][0]["rating"] == 3
    assert data["reviews"][1]["rating"] == 5
