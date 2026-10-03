"""Tests for reservation service authorization rules.

The concurrency/idempotency path is already covered in
``test_reservation_concurrency.py``. This module focuses on the parts
that are awkward to assert under load: per-user ownership rules,
the 404-when-not-authorized behavior, and the pre-lock replay
short-circuit. SSE publish behavior is covered in
``test_realtime.py``.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import hash_password
from ws_core.auth.models import User
from ws_core.errors import ReservationNotFoundError, WorkshopNotFoundError
from ws_core.events import EventBus
from ws_reservation.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from ws_reservation.schemas.reservation import ReservationCreate
from ws_reservation.services import reservation_service

_TEST_PASSWORD_HASH = hash_password("TestPassword123!")


async def _make_user(session: AsyncSession, email: str | None = None) -> User:
    unique = email or f"u_{uuid.uuid4().hex}@example.com"
    user = User(
        full_name=unique.split("@")[0].title(),
        email=unique,
        password_hash=_TEST_PASSWORD_HASH,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_reservation_unknown_workshop_raises(
    session: AsyncSession,
    event_bus: EventBus,
) -> None:
    """POSTing against a random UUID must raise WorkshopNotFoundError."""
    user = await _make_user(session)
    with pytest.raises(WorkshopNotFoundError):
        await reservation_service.create_reservation(
            session=session,
            workshop_id=uuid.uuid4(),
            payload=ReservationCreate(attendee_name="X", attendee_email="x@example.com"),
            idempotency_key=str(uuid.uuid4()),
            user=user,
            event_bus=event_bus,
        )


@pytest.mark.asyncio
async def test_create_reservation_uses_user_identity_when_provided(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """When a user is provided, the reservation is linked to that user."""
    user = await _make_user(session)
    reservation, replayed = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="ignored", attendee_email="ignored@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )
    assert replayed is False
    # The user object wins over the body for the email field.
    assert reservation.attendee_email == user.email


@pytest.mark.asyncio
async def test_create_reservation_ignores_body_identity_entirely(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """The account is authoritative for attendee name and email.

    The body still carries ``attendee_name`` / ``attendee_email`` for
    wire compatibility, but a signed-in caller must not be able to book
    a seat under somebody else's name.
    """
    user = await _make_user(session)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Body Name", attendee_email="body@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )
    assert reservation.attendee_name == user.full_name
    assert reservation.attendee_email == user.email


@pytest.mark.asyncio
async def test_create_reservation_always_sets_owner(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """Every reservation row is owned. This is what makes strict cancel sound."""
    user = await _make_user(session)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="X", attendee_email="x@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )
    row = (
        await session.execute(
            text("SELECT user_id FROM reservations WHERE id = :rid"), {"rid": reservation.id}
        )
    ).scalar()
    assert row == user.id
    assert row is not None


@pytest.mark.asyncio
async def test_cancel_requires_authorization_for_signed_in_user(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """A signed-in user cannot cancel someone else's booking."""
    alice = await _make_user(session)
    bob = await _make_user(session)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Alice", attendee_email=alice.email),
        idempotency_key=str(uuid.uuid4()),
        user=alice,
        event_bus=event_bus,
    )

    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(
            session, reservation.id, bob, event_bus=event_bus
        )


@pytest.mark.asyncio
async def test_cancel_allows_owner_to_cancel_their_own_reservation(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """The owning user can cancel their own reservation."""
    user = await _make_user(session)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Alice", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )
    cancelled = await reservation_service.cancel_reservation(
        session, reservation.id, user, event_bus=event_bus
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None


@pytest.mark.asyncio
async def test_cancel_rejects_signed_in_user_for_unowned_legacy_row(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """No account may cancel a reservation it does not own.

    This inverts the historical rule, which let *any* signed-in
    account cancel *any* ``user_id IS NULL`` row on the theory that
    such rows were "anonymous legacy bookings". Combined with the fact
    that every reservation id was published on the unauthenticated SSE
    channel, that turned the public event stream into a ready-made list
    of bookings any account could cancel. Create now always sets an
    owner, so an unowned row can only be historical data, and it is
    not cancellable by guessing.

    The row is written straight to the table to simulate pre-fix data
    rather than going through the (now owner-requiring) create path.
    """
    legacy_id = uuid.uuid4()
    legacy = Reservation(
        id=legacy_id,
        workshop_id=uuid.UUID(workshop_id),
        user_id=None,
        attendee_name="Legacy",
        attendee_email=f"legacy_{uuid.uuid4().hex}@example.com",
        status=RESERVATION_STATUS_ACTIVE,
    )
    session.add(legacy)
    await session.commit()

    intruder = await _make_user(session)
    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(
            session, legacy_id, intruder, event_bus=event_bus
        )

    # The row must be untouched, not merely hidden.
    await session.refresh(legacy)
    assert legacy.status == RESERVATION_STATUS_ACTIVE
    assert legacy.cancelled_at is None


@pytest.mark.asyncio
async def test_cancel_requires_authentication(
    session: AsyncSession,
    event_bus: EventBus,
) -> None:
    """An anonymous caller cannot cancel any reservation (returns 404, not 500)."""
    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(
            session, uuid.uuid4(), None, event_bus=event_bus
        )


@pytest.mark.asyncio
async def test_cancel_unknown_reservation_raises(
    session: AsyncSession,
    event_bus: EventBus,
) -> None:
    """An unknown reservation id surfaces as ReservationNotFoundError for a signed-in user."""
    user = await _make_user(session)
    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(
            session, uuid.uuid4(), user, event_bus=event_bus
        )


@pytest.mark.asyncio
async def test_deleting_an_account_cannot_orphan_its_reservation(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """The FK must refuse to orphan a reservation by nulling its owner.

    Ownership is enforced strictly at cancel time, so a ``user_id``
    that silently becomes NULL makes the booking uncancellable by
    anyone - including the person who made it. The column is therefore
    ``ON DELETE RESTRICT``: an account holding reservations cannot be
    deleted until they are cancelled.
    """
    from sqlalchemy.exc import IntegrityError

    user = await _make_user(session)
    user_id = user.id
    created, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Owner", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )

    # The FK is immediate, not deferred, so the DELETE itself raises.
    # ``user_id`` is captured above because the rollback expires every
    # ORM instance in the session, and touching one afterwards would
    # attempt a lazy load from sync context.
    with pytest.raises(IntegrityError):
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    await session.rollback()

    # The row keeps its owner and is still the caller's to cancel.
    # create_reservation returns a schema, so re-read the ORM row.
    row = (
        await session.execute(select(Reservation).where(Reservation.id == created.id))
    ).scalar_one()
    assert row.user_id == user_id
    assert row.status == RESERVATION_STATUS_ACTIVE
    owner = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    cancelled = await reservation_service.cancel_reservation(
        session, row.id, owner, event_bus=event_bus
    )
    assert cancelled.status == "cancelled"


def test_reservation_broadcast_dict_carries_no_identifier() -> None:
    """The SSE payload must not expose a reservation id or any PII.

    Every connected browser - including anonymous ones - receives every
    reservation event on the public stream. A reservation id is a
    cancellation capability, so publishing one there handed every
    visitor a list of bookings it could act on. Only ``status``
    survives.
    """
    fake = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        status="active",
        attendee_name="Should Not Leak",
        attendee_email="should-not-leak@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = reservation_service._reservation_to_dict(fake)  # noqa: SLF001
    assert payload == {"status": "active"}
    assert "id" not in payload
    assert "attendee_name" not in payload
    assert "attendee_email" not in payload
    assert "created_at" not in payload


# SSE publish behavior is covered in test_realtime.py (the publish
# helper itself) and in the existing test_reservation_concurrency
# suite (which exercises the full create/cancel path). Keeping the
# reservation-service tests focused on authorization and idempotency
# avoids the test-runner hangs caused by lingering asyncio queues
# across the per-test event loops pytest-asyncio creates.


@pytest.mark.asyncio
async def test_already_reserved_raises_for_same_account(
    session: AsyncSession, workshop_id: str, event_bus: EventBus
) -> None:
    """The same account booking twice in a row raises AlreadyReservedError."""
    from ws_core.errors import AlreadyReservedError

    user = await _make_user(session)
    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
        event_bus=event_bus,
    )
    with pytest.raises(AlreadyReservedError):
        await reservation_service.create_reservation(
            session=session,
            workshop_id=uuid.UUID(workshop_id),
            payload=ReservationCreate(attendee_name="A2", attendee_email=user.email),
            idempotency_key=str(uuid.uuid4()),
            user=user,
            event_bus=event_bus,
        )
    assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_re_reserve_after_cancel_is_allowed(
    session: AsyncSession,
    workshop_id: str,
    event_bus: EventBus,
) -> None:
    """Cancel + re-reserve is the supported 'changed my mind' flow."""
    user = await _make_user(session)
    key1 = str(uuid.uuid4())
    key2 = str(uuid.uuid4())
    first, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=key1,
        user=user,
        event_bus=event_bus,
    )
    await reservation_service.cancel_reservation(session, first.id, user, event_bus=event_bus)

    second, replayed = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=key2,
        user=user,
        event_bus=event_bus,
    )
    assert replayed is False
    assert second.id != first.id
    assert second.status == "active"
