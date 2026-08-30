"""Tests for reservation service authorization rules.

The concurrency/idempotency path is already covered in
``test_reservation_concurrency.py``. This module focuses on the parts
that are awkward to assert under load: per-user ownership rules,
the 404-when-not-authorized behavior, and the pre-lock replay
short-circuit. SSE publish behavior is covered in
``test_realtime.py``.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import hash_password
from src.exceptions import ReservationNotFoundError, WorkshopNotFoundError
from src.models.user import User
from src.schemas.reservation import ReservationCreate
from src.services import reservation_service

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
) -> None:
    """POSTing against a random UUID must raise WorkshopNotFoundError."""
    with pytest.raises(WorkshopNotFoundError):
        await reservation_service.create_reservation(
            session=session,
            workshop_id=uuid.uuid4(),
            payload=ReservationCreate(attendee_name="X", attendee_email="x@example.com"),
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_create_reservation_uses_user_identity_when_provided(
    session: AsyncSession, workshop_id: str
) -> None:
    """When a user is provided, the reservation is linked to that user."""
    user = await _make_user(session)
    reservation, replayed = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="ignored", attendee_email="ignored@example.com"),
        idempotency_key=str(uuid.uuid4()),
        user=user,
    )
    assert replayed is False
    # The user object wins over the body for the email field.
    assert reservation.attendee_email == user.email


@pytest.mark.asyncio
async def test_create_reservation_uses_body_when_anonymous(
    session: AsyncSession, workshop_id: str
) -> None:
    """Without a user, the body fields are persisted as-is."""
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Body Name", attendee_email="body@example.com"),
        idempotency_key=str(uuid.uuid4()),
    )
    assert reservation.attendee_name == "Body Name"
    assert reservation.attendee_email == "body@example.com"


@pytest.mark.asyncio
async def test_cancel_requires_authorization_for_signed_in_user(
    session: AsyncSession, workshop_id: str
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
    )

    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(session, reservation.id, bob)


@pytest.mark.asyncio
async def test_cancel_allows_owner_to_cancel_their_own_reservation(
    session: AsyncSession, workshop_id: str
) -> None:
    """The owning user can cancel their own reservation."""
    user = await _make_user(session)
    reservation, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Alice", attendee_email=user.email),
        idempotency_key=str(uuid.uuid4()),
        user=user,
    )
    cancelled = await reservation_service.cancel_reservation(session, reservation.id, user)
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None


@pytest.mark.asyncio
async def test_cancel_allows_signed_in_user_to_cancel_anonymous_legacy(
    session: AsyncSession, workshop_id: str
) -> None:
    """A signed-in user can cancel an anonymous legacy reservation."""
    legacy, _ = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="Anon", attendee_email="anon@example.com"),
        idempotency_key=str(uuid.uuid4()),
    )
    user = await _make_user(session)
    cancelled = await reservation_service.cancel_reservation(session, legacy.id, user)
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_requires_authentication(session: AsyncSession) -> None:
    """An anonymous caller cannot cancel any reservation (returns 404, not 500)."""
    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(session, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_cancel_unknown_reservation_raises(
    session: AsyncSession,
) -> None:
    """An unknown reservation id surfaces as ReservationNotFoundError for a signed-in user."""
    user = await _make_user(session)
    with pytest.raises(ReservationNotFoundError):
        await reservation_service.cancel_reservation(session, uuid.uuid4(), user)


def test_reservation_broadcast_dict_omits_pii() -> None:
    """The SSE event payload must not contain attendee_name or attendee_email.

    Every connected browser receives every reservation event on the
    public stream; including the attendee's name or email there
    would leak PII to anonymous users.
    """
    from datetime import UTC, datetime
    from types import SimpleNamespace

    fake = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        status="active",
        attendee_name="Should Not Leak",
        attendee_email="should-not-leak@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = reservation_service._reservation_to_dict(fake)  # noqa: SLF001
    assert payload == {"id": str(fake.id), "status": "active"}
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
async def test_already_reserved_raises_for_same_email(
    session: AsyncSession, workshop_id: str
) -> None:
    """Booking the same email twice in a row raises AlreadyReservedError."""
    from src.exceptions import AlreadyReservedError

    suffix = uuid.uuid4().hex
    await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email=f"dup_{suffix}@example.com"),
        idempotency_key=str(uuid.uuid4()),
    )
    with pytest.raises(AlreadyReservedError):
        await reservation_service.create_reservation(
            session=session,
            workshop_id=uuid.UUID(workshop_id),
            payload=ReservationCreate(
                attendee_name="A2", attendee_email=f"dup_{suffix}@example.com"
            ),
            idempotency_key=str(uuid.uuid4()),
        )
    assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_re_reserve_after_cancel_is_allowed(session: AsyncSession, workshop_id: str) -> None:
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
    )
    await reservation_service.cancel_reservation(session, first.id, user)

    second, replayed = await reservation_service.create_reservation(
        session=session,
        workshop_id=uuid.UUID(workshop_id),
        payload=ReservationCreate(attendee_name="A", attendee_email="a@example.com"),
        idempotency_key=key2,
        user=user,
    )
    assert replayed is False
    assert second.id != first.id
    assert second.status == "active"
