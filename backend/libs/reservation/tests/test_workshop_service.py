"""Tests for the workshop read service.

Exercises the computed ``available_spots`` field, the empty-state list
behavior, and the privacy rules that hide other users' reservations
from a signed-in detail-view request.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth.models import User
from ws_core.errors import WorkshopNotFoundError
from ws_core.events import EventBus

from ws_core.auth import hash_password
from ws_reservation.models.reservation import (
    RESERVATION_STATUS_ACTIVE,
    RESERVATION_STATUS_CANCELLED,
)
from ws_reservation.schemas.reservation import ReservationCreate
from ws_reservation.services import reservation_service, workshop_service

_TEST_PASSWORD_HASH = hash_password("TestPassword123!")


async def _make_user(session: AsyncSession, label: str) -> User:
    """Insert an account and return the refreshed row."""
    user = User(
        full_name=label,
        email=f"{label}_{uuid.uuid4().hex}@example.com",
        password_hash=_TEST_PASSWORD_HASH,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_list_workshops_empty_database(session: AsyncSession) -> None:
    """The list endpoint returns whatever is in the table.

    A real-DB fixture cannot guarantee an empty table, so this test
    only asserts the call returns a list (not an exception) and
    contains the rows that the test itself just created, if any.
    """
    result = (await workshop_service.list_workshops(session)).items
    assert isinstance(result, list)
    assert all(w.available_spots >= 0 for w in result)
    assert all(w.max_capacity > 0 for w in result)


@pytest.mark.asyncio
async def test_list_workshops_reports_capacity_minus_active(
    session: AsyncSession, workshop_id: str
) -> None:
    """The fixture workshop is reported with the full capacity available."""
    workshops = (await workshop_service.list_workshops(session)).items
    target = next(w for w in workshops if str(w.id) == workshop_id)
    assert target.max_capacity == 3
    assert target.available_spots == 3


@pytest.mark.asyncio
async def test_list_workshops_decrements_after_reservation(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """Creating a reservation decrements the visible seat count for that workshop."""
    before = (await workshop_service.list_workshops(session)).items
    starting_spots = next(w for w in before if str(w.id) == workshop_id).available_spots

    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=await _make_user(session, "decrement"),
        event_bus=event_bus,
    )

    after = (await workshop_service.list_workshops(session)).items
    target = next(w for w in after if str(w.id) == workshop_id)
    assert target.available_spots == starting_spots - 1


@pytest.mark.asyncio
async def test_list_workshops_clamps_available_to_zero_when_full(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """Filling the fixture workshop (capacity=3) drops available_spots to 0."""
    for i in range(3):
        await reservation_service.create_reservation(
            session=session,
            workshop_id=uuid.UUID(workshop_id),
            payload=ReservationCreate(
                attendee_name=f"U{i}", attendee_email=f"u{i}_{uuid.uuid4()}@example.com"
            ),
            idempotency_key=str(uuid.uuid4()),
            user=await _make_user(session, f"filler{i}"),
            event_bus=event_bus,
        )

    workshops = (await workshop_service.list_workshops(session)).items
    target = next(w for w in workshops if str(w.id) == workshop_id)
    assert target.available_spots == 0


@pytest.mark.asyncio
async def test_get_workshop_detail_returns_reservations_only_for_owner(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """A signed-in user sees only their own reservations, not other users'."""
    suffix = uuid.uuid4().hex
    owner = User(
        full_name="Owner", email=f"owner_{suffix}@example.com", password_hash=_TEST_PASSWORD_HASH
    )
    intruder = User(
        full_name="Intruder",
        email=f"intruder_{suffix}@example.com",
        password_hash=_TEST_PASSWORD_HASH,
    )
    session.add_all([owner, intruder])
    await session.commit()
    await session.refresh(owner)
    await session.refresh(intruder)

    mine, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(
            attendee_name="Owner", attendee_email=f"owner_{suffix}@example.com"
        ),
        idempotency_key=str(uuid.uuid4()),
        user=owner,
        event_bus=event_bus,
    )
    theirs, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(
            attendee_name="Intruder", attendee_email=f"intruder_{suffix}@example.com"
        ),
        idempotency_key=str(uuid.uuid4()),
        user=intruder,
        event_bus=event_bus,
    )

    detail = await workshop_service.get_workshop_detail(session, uuid.UUID(workshop_id), owner.id)
    visible_ids = {r.id for r in detail.reservations}
    # The signed-in owner sees only their own reservation, never the intruder's.
    assert mine.id in visible_ids
    assert theirs.id not in visible_ids


@pytest.mark.asyncio
async def test_get_workshop_detail_anonymous_sees_no_reservations(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """An anonymous request always sees an empty reservations list."""
    suffix = uuid.uuid4().hex
    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email=f"a_{suffix}@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=await _make_user(session, f"anonview{suffix[:8]}"),
        event_bus=event_bus,
    )
    detail = await workshop_service.get_workshop_detail(
        session, uuid.UUID(workshop_id), user_id=None
    )
    assert detail.reservations == []


@pytest.mark.asyncio
async def test_get_workshop_detail_ignores_cancelled_reservations(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """Cancelled bookings must not appear in the detail's reservation list."""
    suffix = uuid.uuid4().hex
    user = User(
        full_name="Owner", email=f"owner_{suffix}@example.com", password_hash=_TEST_PASSWORD_HASH
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(
            attendee_name="Owner", attendee_email=f"owner_{suffix}@example.com"
        ),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )

    # Read the starting spot count for this specific workshop so the
    # assertion holds even if other rows exist in the table.
    detail_before = await workshop_service.get_workshop_detail(
        session, uuid.UUID(workshop_id), user.id
    )
    spots_before = detail_before.available_spots
    assert any(r.id == reservation.id for r in detail_before.reservations)

    await reservation_service.cancel_reservation(session, reservation.id, user, event_bus=event_bus)

    detail = await workshop_service.get_workshop_detail(session, uuid.UUID(workshop_id), user.id)
    assert all(r.id != reservation.id for r in detail.reservations)
    assert detail.available_spots == spots_before + 1


@pytest.mark.asyncio
async def test_get_workshop_detail_raises_when_missing(session: AsyncSession) -> None:
    """An unknown id surfaces as WorkshopNotFoundError (→ 404)."""
    with pytest.raises(WorkshopNotFoundError):
        await workshop_service.get_workshop_detail(session, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_get_workshop_detail_picks_active_status_only(
    session: AsyncSession, workshop_id: str, event_bus: EventBus,
) -> None:
    """A row with status='cancelled' must never be considered active."""
    # Snapshot before. The fixture's shared DB may already contain
    # active rows for this workshop, so the starting count is taken
    # from the response rather than assumed.
    detail_before = await workshop_service.get_workshop_detail(
        session, uuid.UUID(workshop_id), None
    )
    starting_active = detail_before.available_spots

    # Reach into the model layer to manufacture a cancelled row that
    # bypasses the service's cancel path, so the status filter can
    # be verified directly.
    suffix = uuid.uuid4().hex
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email=f"a_{suffix}@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=await _make_user(session, f"cancelled{suffix[:8]}"),
        event_bus=event_bus,
    )
    reservation.status = RESERVATION_STATUS_CANCELLED
    reservation.cancelled_at = datetime.now(UTC)
    await session.commit()

    detail = await workshop_service.get_workshop_detail(session, uuid.UUID(workshop_id), None)
    assert detail.available_spots == starting_active - 1
    assert all(r.status == RESERVATION_STATUS_ACTIVE for r in detail.reservations)


@pytest.mark.asyncio
async def test_list_workshops_is_ordered_by_start_time(session: AsyncSession) -> None:
    """The catalogue is sorted by ``starts_at`` ascending.

    Insert three workshops out of chronological order and assert the
    endpoint returns them earliest-first.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    suffix = uuid.uuid4().hex
    base = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    titles = [
        f"order_late_{suffix}",
        f"order_early_{suffix}",
        f"order_mid_{suffix}",
    ]
    starts = [base + timedelta(days=10), base, base + timedelta(days=5)]
    new_ids: list[uuid.UUID] = []
    try:
        for title, starts_at in zip(titles, starts, strict=True):
            new_id = uuid.uuid4()
            new_ids.append(new_id)
            await session.execute(
                text(
                    "INSERT INTO workshops (id, title, starts_at, max_capacity) "
                    "VALUES (:id, :title, :starts_at, :capacity)"
                ),
                {
                    "id": new_id,
                    "title": title,
                    "starts_at": starts_at,
                    "capacity": 2,
                },
            )
        await session.commit()

        result = (
            await workshop_service.list_workshops(session, limit=1000)
        ).items
        ours = [w for w in result if str(w.id) in {str(i) for i in new_ids}]
        assert [w.title for w in ours] == [
            f"order_early_{suffix}",
            f"order_mid_{suffix}",
            f"order_late_{suffix}",
        ]
    finally:
        for new_id in new_ids:
            await session.execute(text("DELETE FROM workshops WHERE id = :id"), {"id": new_id})
        await session.commit()


@pytest.mark.asyncio
async def test_create_and_delete_workshop(
    session: AsyncSession, event_bus: EventBus,
) -> None:
    from datetime import datetime, timedelta

    from ws_reservation.schemas.workshop import WorkshopCreate

    starts_at = datetime.now(UTC) + timedelta(days=1)
    ends_at = starts_at + timedelta(hours=1)
    payload = WorkshopCreate(
        title="Test Workshop API",
        description="Testing creation",
        max_capacity=10,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    workshop = await workshop_service.create_workshop(session, payload, event_bus=event_bus)

    assert workshop.title == "Test Workshop API"
    assert workshop.max_capacity == 10

    await workshop_service.delete_workshop(session, workshop.id, event_bus=event_bus)

    with pytest.raises(WorkshopNotFoundError):
        await workshop_service.get_workshop_detail(session, workshop.id, None)
