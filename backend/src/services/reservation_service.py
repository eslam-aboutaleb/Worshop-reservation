"""Write-side service functions for the reservation surface.

The three public coroutines here own the **only** mutations to the
``reservations`` and ``idempotency_keys`` tables. They are the place
where the central invariants of the system are enforced:

* **Capacity is enforced by a row lock** on the parent workshop
  (``SELECT ... FOR UPDATE``) inside the create transaction. See
  :func:`create_reservation` for the exact sequence.
* **Idempotency is enforced by the ``idempotency_keys`` table** and
  by the partial unique index ``uq_active_reservation`` on
  ``(workshop_id, attendee_email) WHERE status='active'``. The
  partial index is a backstop: the key check is the first line.
* **Cancellation is idempotent.** A second ``DELETE`` returns 200
  with the existing cancelled row instead of 409.
* **Ownership is strict.** Every reservation has a non-null
  ``user_id`` (create requires authentication) and a cancel is
  refused unless that ``user_id`` matches the caller. There is no
  "unowned row is cancelable by anyone" path.

Every successful mutation publishes an event on the SSE channel via
``realtime.publish`` so connected browsers see live updates. Those
events carry counts only: the channel is unauthenticated, so no
reservation identifier is ever broadcast on it.
"""

import uuid

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import (
    AlreadyReservedError,
    ReservationNotFoundError,
    WorkshopFullError,
    WorkshopNotFoundError,
)
from src.models.idempotency_key import IdempotencyKey
from src.models.reservation import (
    RESERVATION_STATUS_ACTIVE,
    RESERVATION_STATUS_CANCELLED,
    Reservation,
)
from src.models.user import User
from src.models.workshop import Workshop
from src.realtime import publish
from src.schemas.reservation import (
    ReservationCancelResponse,
    ReservationCreate,
    ReservationResponse,
)
from src.services.reservation_queries import (
    count_active_reservations,
    find_idempotent_reservation,
)

logger = structlog.get_logger(__name__)


def _reservation_to_dict(reservation: Reservation) -> dict:
    """Build the per-reservation portion of the SSE broadcast payload.

    The broadcast channel is **public**: ``GET /api/workshops/events``
    requires no credentials, so every anonymous visitor sees every
    event. Only the reservation's ``status`` is emitted.

    The reservation ``id`` is deliberately excluded. An id is a
    capability: it is the only value a caller needs to act on a
    reservation row, and publishing it on an unauthenticated channel
    handed every logged-in account a ready-made list of ids to cancel.
    Clients that legitimately hold a reservation obtain its id from
    ``GET /api/reservations/me`` or the authenticated workshop detail,
    both of which are scoped to the caller.

    Args:
        reservation: The reservation being reported.

    Returns:
        A dict with primitive types so it survives ``json.dumps``.
    """
    return {"status": reservation.status}


async def create_reservation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    payload: ReservationCreate,
    idempotency_key: str,
    user: User,
) -> tuple[ReservationResponse, bool]:
    """Create a reservation under a row-locked workshop.

    The high-level flow is:

        1. Cheap idempotency-key replay check (no lock).
        2. ``SELECT ... FOR UPDATE`` on the workshop to serialize
           capacity checks and inserts for this workshop only.
        3. Re-check the idempotency key under the lock (a
           concurrent retry may have committed while the lock
           was being acquired).
        4. Refuse if the attendee already has an active booking.
        5. Refuse if the workshop is at capacity.
        6. Insert the reservation and the idempotency record.
        7. Broadcast a ``reservation_created`` event.

    Steps 1-5 live in dedicated helpers below so the top-level
    function reads as the orchestrator it is; each helper owns one
    concern and one set of error types. Tests cover the same flows
    they have always covered; see
    ``tests/test_reservation_concurrency.py`` and
    ``tests/test_reservation_service.py``.

    Args:
        session: Active async database session.
        workshop_id: Target workshop.
        payload: Validated reservation request body. Its
            ``attendee_name`` / ``attendee_email`` are accepted for
            wire compatibility but are **ignored**: the authenticated
            account is the sole source of attendee identity, so a
            caller cannot book a seat under somebody else's name.
        idempotency_key: Client-supplied idempotency token.
        user: The signed-in account that will own the reservation.
            Required. Every reservation therefore has a non-null
            ``user_id``, which is what makes the strict ownership
            check in :func:`cancel_reservation` sound.

    Returns:
        Tuple of ``(reservation response, replayed flag)``. ``replayed``
        is ``True`` when the response comes from an idempotency-key
        match; the router uses this to return 200 instead of 201.

    Raises:
        WorkshopNotFoundError: If the workshop does not exist.
        WorkshopFullError: If the workshop is at capacity.
        AlreadyReservedError: If the attendee already has an active
            booking for this workshop.
    """
    # Fast, unlocked idempotency replay check to short-circuit repeated requests
    replay = await _replay_idempotent(session, workshop_id, idempotency_key, user.id)
    if replay is not None:
        return replay, True

    try:
        # Pessimistic row lock (SELECT ... FOR UPDATE) on the workshop so
        # capacity checks and inserts for this workshop are serialized.
        workshop = await _lock_workshop(session, workshop_id)

        # Re-check the key under the lock: a racing request may have
        # committed while we were waiting to acquire it.
        replay = await _replay_idempotent(session, workshop_id, idempotency_key, user.id)
        if replay is not None:
            await session.rollback()
            return replay, True

        # Ensure the attendee doesn't already have an active booking for this workshop
        attendee_email = user.email
        await _reject_duplicate_attendee(session, workshop_id, attendee_email)

        # Verify remaining capacity before admitting the new reservation
        active_count = await count_active_reservations(session, workshop_id)
        if active_count >= workshop.max_capacity:
            raise WorkshopFullError(str(workshop_id))

        # Create and stage the new reservation and its idempotency record
        reservation = _new_reservation(workshop_id, user, attendee_email)
        await _insert_reservation(session, reservation, workshop_id)
        await _persist_idempotency_key(
            session, idempotency_key, workshop_id, user.id, reservation.id
        )

        try:
            # Commit the transaction, persisting changes and releasing the workshop row lock
            await session.commit()
        except IntegrityError:
            # Handle race condition where a duplicate idempotency key committed concurrently
            await session.rollback()
            replay = await _replay_idempotent(session, workshop_id, idempotency_key, user.id)
            if replay is not None:
                return replay, True
            raise
    except (AlreadyReservedError, WorkshopFullError, WorkshopNotFoundError):
        # Always release the database row lock on expected validation errors
        await session.rollback()
        raise

    # Step 7: Refresh model attributes from the database and broadcast real-time event
    await session.refresh(reservation)
    await _broadcast_creation(workshop, reservation, active_count)
    logger.info(
        "reservation.created",
        workshop_id=str(workshop_id),
        reservation_id=str(reservation.id),
        user_id=str(user.id),
    )
    return ReservationResponse.model_validate(reservation), False


async def _replay_idempotent(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    idempotency_key: str,
    user_id: uuid.UUID,
) -> ReservationResponse | None:
    """Return the caller's prior reservation for this key+workshop, if any.

    The lookup is scoped to ``user_id`` on purpose. Scoping only on
    ``(key, workshop_id)`` would let any account that guessed or
    replayed another caller's key read back that caller's reservation,
    including the attendee name and email carried on
    ``ReservationResponse``.
    """
    existing = await find_idempotent_reservation(session, workshop_id, idempotency_key, user_id)
    if existing is None:
        return None
    return ReservationResponse.model_validate(existing)


async def _lock_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> Workshop:
    """Row-lock the workshop row and return it, or 404 if missing."""
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))
    return workshop


async def _reject_duplicate_attendee(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    attendee_email: str,
) -> None:
    """Raise if ``attendee_email`` already has an active booking here."""
    duplicate = (
        await session.execute(
            select(Reservation)
            .where(Reservation.workshop_id == workshop_id)
            .where(Reservation.attendee_email == attendee_email)
            .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise AlreadyReservedError(str(workshop_id))


def _new_reservation(
    workshop_id: uuid.UUID,
    user: User,
    attendee_email: str,
) -> Reservation:
    """Build a new in-memory ``Reservation`` ORM object.

    The attendee identity comes from the authenticated account only,
    so ``user_id`` is always populated and never left to a client
    supplied value.
    """
    return Reservation(
        workshop_id=workshop_id,
        attendee_name=user.full_name,
        attendee_email=attendee_email,
        user_id=user.id,
        status=RESERVATION_STATUS_ACTIVE,
    )


async def _insert_reservation(
    session: AsyncSession,
    reservation: Reservation,
    workshop_id: uuid.UUID,
) -> None:
    """Stage the new reservation row. The partial unique index may trip
    on a race; the failure is surfaced as ``WorkshopFullError`` so the
    API surface stays minimal (a duplicate-by-email race is
    semantically the same outcome as the workshop being full at
    that instant).
    """
    session.add(reservation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise WorkshopFullError(str(workshop_id)) from exc


async def _persist_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID,
    reservation_id: uuid.UUID,
) -> None:
    """Stage the idempotency record.

    The row is keyed by ``(key, workshop_id, user_id)``, so a collision
    can only ever come from the *same* account retrying the same
    request on the same workshop - which is exactly the case the
    replay check above already resolved. A duplicate here is
    therefore a genuine concurrent retry; the reservation row is not
    visible until commit, so this helper cannot detect the winner and
    leaves the decision to the orchestrator.
    """
    session.add(
        IdempotencyKey(
            key=idempotency_key,
            workshop_id=workshop_id,
            user_id=user_id,
            reservation_id=reservation_id,
        )
    )


async def _broadcast_creation(
    workshop: Workshop,
    reservation: Reservation,
    active_count_before_insert: int,
) -> None:
    """Publish the ``reservation_created`` SSE event.

    ``active_count_before_insert`` is the active count observed
    while holding the row lock; the just-inserted reservation is
    not included in that count, so the post-insert available is
    ``max_capacity - (active_count + 1)`` clamped to zero.
    """
    await publish(
        reservation.workshop_id,
        {
            "workshop_id": str(reservation.workshop_id),
            "type": "reservation_created",
            "available_spots": max(workshop.max_capacity - active_count_before_insert - 1, 0),
            "reservation": _reservation_to_dict(reservation),
        },
    )


async def cancel_reservation(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    user: User | None = None,
) -> ReservationCancelResponse:
    """Cancel a reservation. Idempotent on a second call.

    Authorization: a valid signed-in account is **required**, and the
    reservation must be owned by that account. An anonymous caller,
    a non-owner, and an unowned (``user_id IS NULL``) row all
    surface as ``ReservationNotFoundError`` (404) so the response
    does not leak whether a reservation id exists. The first
    ``select`` doubles as the existence/authorization check; the
    second conditional ``update`` is the actual cancel and is the
    only path that can publish the SSE event.

    Args:
        session: Active async database session.
        reservation_id: Reservation to cancel.
        user: Signed-in account. ``None`` is rejected with 404 to
            remove the legacy "anyone with the id can cancel"
            vector.

    Returns:
        A ``ReservationCancelResponse`` for the cancelled (or
        already-cancelled) reservation.

    Raises:
        ReservationNotFoundError: If no matching reservation exists,
            the caller is anonymous, or the caller does not own
            the reservation.
    """
    if user is None:
        raise ReservationNotFoundError(str(reservation_id))
    existing = (
        await session.execute(select(Reservation).where(Reservation.id == reservation_id))
    ).scalar_one_or_none()
    if existing is None:
        raise ReservationNotFoundError(str(reservation_id))
    if not _is_authorized_to_cancel(existing, user):
        raise ReservationNotFoundError(str(reservation_id))

    # Fast path: already cancelled → return the existing row (200).
    if existing.status == RESERVATION_STATUS_CANCELLED:
        return ReservationCancelResponse.model_validate(existing)

    # Conditional update so two concurrent cancels cannot both win.
    # `synchronize_session=False` lets us avoid the identity-map
    # refresh; `existing` is then refreshed explicitly afterwards. The
    # authorization predicate mirrors `_is_authorized_to_cancel` so
    # the database enforces the same strict-ownership rule even if the
    # in-memory check above is ever bypassed.
    stmt = (
        update(Reservation)
        .where(Reservation.id == reservation_id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        .where(Reservation.user_id == user.id)
    )
    result = await session.execute(
        stmt.values(status=RESERVATION_STATUS_CANCELLED, cancelled_at=func.now())
        .returning(Reservation)
        .execution_options(synchronize_session=False)
    )
    cancelled = result.scalar_one_or_none()
    if cancelled is None:
        # Lost the race to another cancel that committed first.
        # Re-read the now-cancelled row and return it.
        await session.refresh(existing)
        if existing.status == RESERVATION_STATUS_CANCELLED:
            return ReservationCancelResponse.model_validate(existing)
        raise ReservationNotFoundError(str(reservation_id))

    # The ORM instance returned from `update()...returning()` is the
    # same Python object as `existing`; refresh it so the returned
    # payload reflects the new status / cancelled_at.
    await session.refresh(cancelled)
    workshop_id = cancelled.workshop_id
    await session.commit()
    await _broadcast_cancellation(session, workshop_id)
    logger.info(
        "reservation.cancelled",
        workshop_id=str(workshop_id),
        reservation_id=str(cancelled.id),
        user_id=str(user.id) if user else None,
    )
    return ReservationCancelResponse.model_validate(cancelled)


def _is_authorized_to_cancel(reservation: Reservation, user: User) -> bool:
    """Return True if ``user`` is the owner of ``reservation``.

    Ownership is strict: the reservation's ``user_id`` must equal the
    caller's id. There is deliberately **no** ``user_id IS NULL``
    carve-out. Such a carve-out (added for historical
    "anonymous reservation" rows) handed every authenticated account
    the right to cancel every unowned booking, which was reachable in
    practice because :func:`create_reservation` used to accept
    anonymous callers.

    An anonymous request must never resolve here at all; the caller
    rejects ``user is None`` before this function runs.

    Args:
        reservation: The reservation being cancelled.
        user: The signed-in account. Must not be ``None``.

    Returns:
        True only when the caller owns the reservation.
    """
    return reservation.user_id == user.id


async def _broadcast_cancellation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
) -> None:
    """Publish a ``reservation_cancelled`` event with current spot count.

    Recomputes the active reservation count post-cancel so subscribers
    see the new available total without polling the detail endpoint.

    The cancelled reservation's ``id`` is deliberately omitted. The
    channel is public, and an id is a cancellation capability; see
    :func:`_reservation_to_dict`.

    Args:
        session: Active async database session.
        workshop_id: Workshop whose count changed.
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id))
    ).scalar_one()
    active = await count_active_reservations(session, workshop_id)
    await publish(
        workshop_id,
        {
            "workshop_id": str(workshop_id),
            "type": "reservation_cancelled",
            "available_spots": max(workshop.max_capacity - active, 0),
        },
    )
