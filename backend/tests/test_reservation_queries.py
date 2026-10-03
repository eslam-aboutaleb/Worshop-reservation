"""Tests for the read-side reservation query helpers.

These cover the lower-level query functions used by both the service
layer and the routers. They are the public surface for "give me the
count / find this key / list mine" and are easier to assert against
in isolation than the higher-level create flow.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth.models import User

from src.auth import hash_password
from src.schemas.reservation import ReservationCreate
from src.services import reservation_queries, reservation_service

_TEST_PASSWORD_HASH = hash_password("TestPassword123!")


def _unique_user(session: AsyncSession, label: str) -> User:
    """Create a user with a globally-unique email."""
    suffix = uuid.uuid4().hex
    user = User(
        full_name=label,
        email=f"{label}_{suffix}@example.com",
        password_hash=_TEST_PASSWORD_HASH,
    )
    session.add(user)
    return user


@pytest.mark.asyncio
async def test_count_active_reservations_starts_at_zero(
    session: AsyncSession, workshop_id: str
) -> None:
    """A fresh workshop has no active reservations."""
    count = await reservation_queries.count_active_reservations(session, uuid.UUID(workshop_id))
    assert count == 0


@pytest.mark.asyncio
async def test_count_active_reservations_excludes_cancelled(
    session: AsyncSession, workshop_id: str
) -> None:
    """Cancelled rows must not be counted toward the active total."""
    user = _unique_user(session, "countuser")
    await session.commit()
    await session.refresh(user)

    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="U", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
    )
    assert await reservation_queries.count_active_reservations(session, uuid.UUID(workshop_id)) == 1

    await reservation_service.cancel_reservation(session, reservation.id, user)
    assert await reservation_queries.count_active_reservations(session, uuid.UUID(workshop_id)) == 0


@pytest.mark.asyncio
async def test_find_idempotent_reservation_returns_none_when_absent(
    session: AsyncSession, workshop_id: str
) -> None:
    """An unknown key returns None (not an exception)."""
    user = _unique_user(session, "absent")
    await session.commit()
    await session.refresh(user)

    result = await reservation_queries.find_idempotent_reservation(
        session, uuid.UUID(workshop_id), str(uuid.uuid4()), user.id
    )
    assert result is None


@pytest.mark.asyncio
async def test_find_idempotent_reservation_returns_linked_reservation(
    session: AsyncSession, workshop_id: str
) -> None:
    """The key points back at the reservation created in the same call."""
    user = _unique_user(session, "linked")
    await session.commit()
    await session.refresh(user)
    key = str(uuid.uuid4())
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=key,
        user=user,
    )

    found = await reservation_queries.find_idempotent_reservation(
        session, uuid.UUID(workshop_id), key, user.id
    )
    assert found is not None
    assert found.id == reservation.id


@pytest.mark.asyncio
async def test_find_idempotent_reservation_is_scoped_to_workshop(
    session: AsyncSession, workshop_id: str
) -> None:
    """The same key on a different workshop must not match."""
    user = _unique_user(session, "scoped")
    await session.commit()
    await session.refresh(user)
    key = str(uuid.uuid4())
    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=key,
        user=user,
    )

    other_workshop_id = uuid.uuid4()
    found = await reservation_queries.find_idempotent_reservation(
        session, other_workshop_id, key, user.id
    )
    assert found is None


@pytest.mark.asyncio
async def test_find_idempotent_reservation_is_scoped_to_caller(
    session: AsyncSession, workshop_id: str
) -> None:
    """Reusing another account's key must not resolve to their reservation.

    The key is a client-supplied opaque string, not a credential. If
    the lookup ignored the caller, any account that replayed a
    victim's key would read back the victim's reservation - attendee
    name and email included - straight from the replay response.
    """
    owner = _unique_user(session, "keyowner")
    attacker = _unique_user(session, "keythief")
    await session.commit()
    await session.refresh(owner)
    await session.refresh(attacker)
    key = str(uuid.uuid4())
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Owner", attendee_email="owner@example.com"),
        idempotency_key=key,
        user=owner,
    )

    leaked = await reservation_queries.find_idempotent_reservation(
        session, uuid.UUID(workshop_id), key, attacker.id
    )
    assert leaked is None
    assert leaked is not reservation


@pytest.mark.asyncio
async def test_list_user_reservations_returns_only_owned_rows(
    session: AsyncSession, workshop_id: str
) -> None:
    """A user's listing contains only their own reservations, newest first."""
    user = _unique_user(session, "listowner")
    other = _unique_user(session, "listother")
    await session.commit()
    await session.refresh(user)
    await session.refresh(other)

    mine, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Owner", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
    )
    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Other", attendee_email=other.email),
        idempotency_key=str(uuid.uuid4()),
        user=other,
    )

    rows = await reservation_queries.list_user_reservations(session, user)
    reservations = [r for r, _title in rows]
    titles = [title for _r, title in rows]
    assert len(reservations) == 1
    assert reservations[0].id == mine.id
    assert titles[0] == "API test workshop"


@pytest.mark.asyncio
async def test_list_user_reservations_empty_for_new_user(session: AsyncSession) -> None:
    """A user with no reservations gets an empty list (not an error)."""
    user = _unique_user(session, "freshuser")
    await session.commit()
    await session.refresh(user)

    rows = await reservation_queries.list_user_reservations(session, user)
    assert rows == []


@pytest.mark.asyncio
async def test_list_user_reservations_includes_cancelled_rows(
    session: AsyncSession, workshop_id: str
) -> None:
    """The /me listing shows both active and cancelled reservations."""
    user = _unique_user(session, "canceluser")
    await session.commit()
    await session.refresh(user)

    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="U", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
    )
    await reservation_service.cancel_reservation(session, reservation.id, user)

    rows = await reservation_queries.list_user_reservations(session, user)
    assert len(rows) == 1
    assert rows[0][0].status == "cancelled"
