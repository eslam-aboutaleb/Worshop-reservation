"""Concurrency and idempotency tests for the reservation service.

These tests use a real PostgreSQL because the partial unique index
and `SELECT ... FOR UPDATE` semantics are not reproducible on SQLite.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.auth import hash_password
from src.models.idempotency_key import IdempotencyKey
from src.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from src.models.user import User
from src.schemas.reservation import ReservationCreate
from src.services import reservation_service

_engine = create_async_engine(
    os.environ["DATABASE_URL"],
    echo=False,
    poolclass=NullPool,
)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
_TEST_PASSWORD_HASH = hash_password("TestPassword123!")


def _payload(email: str) -> ReservationCreate:
    """Build a valid ReservationCreate for tests."""
    return ReservationCreate(attendee_name="Tester", attendee_email=email)


async def _make_user(email: str) -> User:
    """Insert a fresh user row and return the ORM instance."""
    async with _session_factory() as session:
        user = User(
            full_name="Tester",
            email=email.lower(),
            password_hash=_TEST_PASSWORD_HASH,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_concurrent_reservations_yield_exactly_one_success(workshop) -> None:
    """Capacity=1, 50 concurrent reserves → exactly one 201, 49 409s, 1 row."""
    results: list[tuple[int, object]] = []

    async def attempt(idx: int) -> None:
        async with _session_factory() as session:
            try:
                response, _replayed = await reservation_service.create_reservation(
                    session=session,
                    workshop_id=workshop.id,
                    payload=_payload(f"user{idx}@example.com"),
                    idempotency_key=str(uuid.uuid4()),
                )
                results.append((201, response))
            except Exception as exc:  # noqa: BLE001: tests inspect the exception type
                results.append((409, exc))

    workers = [asyncio.create_task(attempt(i)) for i in range(50)]
    await asyncio.gather(*workers)

    successes = [r for r in results if r[0] == 201]
    conflicts = [r for r in results if r[0] == 409]
    assert len(successes) == 1, f"expected 1 success, got {len(successes)}: {results}"
    assert len(conflicts) == 49, f"expected 49 conflicts, got {len(conflicts)}"

    async with _session_factory() as session:
        count = (
            (
                await session.execute(
                    select(Reservation).where(Reservation.workshop_id == workshop.id)
                )
            )
            .scalars()
            .all()
        )
    active = [r for r in count if r.status == RESERVATION_STATUS_ACTIVE]
    assert len(active) == 1


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_reservation(workshop) -> None:
    """Same key, same workshop → same reservation returned twice."""
    key = str(uuid.uuid4())
    payload = _payload("replay@example.com")

    async with _session_factory() as session:
        first, replayed_first = await reservation_service.create_reservation(
            session=session, workshop_id=workshop.id, payload=payload, idempotency_key=key
        )
    async with _session_factory() as session:
        second, replayed_second = await reservation_service.create_reservation(
            session=session, workshop_id=workshop.id, payload=payload, idempotency_key=key
        )
    assert replayed_first is False
    assert replayed_second is True
    assert first.id == second.id

    async with _session_factory() as session:
        keys = (
            (
                await session.execute(
                    select(IdempotencyKey).where(IdempotencyKey.workshop_id == workshop.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(keys) == 1


@pytest.mark.asyncio
async def test_concurrent_same_key_replays_when_first_request_fills_workshop(workshop) -> None:
    """A concurrent retry must replay even after the first request uses the last seat."""
    key = str(uuid.uuid4())
    barrier = asyncio.Barrier(2)
    results: list[tuple[str, object]] = []

    async def attempt(email: str) -> None:
        async with _session_factory() as session:
            await barrier.wait()
            try:
                response, replayed = await reservation_service.create_reservation(
                    session=session,
                    workshop_id=workshop.id,
                    payload=_payload(email),
                    idempotency_key=key,
                )
                results.append(("replayed" if replayed else "created", response))
            except Exception as exc:  # noqa: BLE001 - test asserts no conflict occurs
                results.append(("error", exc))

    await asyncio.gather(
        attempt("first@example.com"),
        attempt("retry@example.com"),
    )

    assert sorted(result[0] for result in results) == ["created", "replayed"]
    assert results[0][1].id == results[1][1].id


@pytest.mark.asyncio
async def test_cancel_then_replay_does_not_re_reserve(workshop) -> None:
    """Cancelling frees a seat, but the same idempotency key still replays."""
    key = str(uuid.uuid4())
    email = f"a_{uuid.uuid4().hex}@example.com"
    user = await _make_user(email)
    async with _session_factory() as session:
        created, _ = await reservation_service.create_reservation(
            session=session,
            workshop_id=workshop.id,
            payload=_payload(email),
            idempotency_key=key,
            user=user,
        )
        await reservation_service.cancel_reservation(session, created.id, user)
    async with _session_factory() as session:
        replayed, replayed_flag = await reservation_service.create_reservation(
            session=session,
            workshop_id=workshop.id,
            payload=_payload(email),
            idempotency_key=key,
            user=user,
        )
    assert replayed.id == created.id
    assert replayed_flag is True


@pytest.mark.asyncio
async def test_cancel_is_idempotent(workshop) -> None:
    """Second cancel returns 200 with the existing cancelled_at."""
    key = str(uuid.uuid4())
    email = f"b_{uuid.uuid4().hex}@example.com"
    user = await _make_user(email)
    async with _session_factory() as session:
        created, _ = await reservation_service.create_reservation(
            session=session,
            workshop_id=workshop.id,
            payload=_payload(email),
            idempotency_key=key,
            user=user,
        )
        first_cancel = await reservation_service.cancel_reservation(session, created.id, user)
        second_cancel = await reservation_service.cancel_reservation(session, created.id, user)
    assert first_cancel.status == "cancelled"
    assert second_cancel.status == "cancelled"
    assert second_cancel.cancelled_at is not None


@pytest.mark.asyncio
async def test_email_normalization_treats_case_as_same(workshop) -> None:
    """Different cases of the same email are rejected as duplicates."""
    from src.exceptions import AlreadyReservedError

    async with _session_factory() as session:
        await reservation_service.create_reservation(
            session=session,
            workshop_id=workshop.id,
            payload=_payload("Alice@Example.com"),
            idempotency_key=str(uuid.uuid4()),
        )
    async with _session_factory() as session:
        with pytest.raises(AlreadyReservedError):
            await reservation_service.create_reservation(
                session=session,
                workshop_id=workshop.id,
                payload=_payload("alice@example.com"),
                idempotency_key=str(uuid.uuid4()),
            )
