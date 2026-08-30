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

Every successful mutation publishes an event on the SSE channel via
``realtime.publish`` so connected browsers see live updates.
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
    """Convert a ``Reservation`` row to the JSON payload broadcast over SSE.

    The broadcast channel is public: every connected browser sees
    every event, so the attendee's name and email (PII) are
    deliberately omitted. The detail endpoint embeds the same data
    scoped to the signed-in caller.

    Args:
        reservation: The reservation to serialize.

    Returns:
        A dict with primitive types so it survives ``json.dumps``.
        The id is the only field that could be considered user
        data and it is already required by clients that want to
        cross-reference with the detail view.
    """
    return {
        "id": str(reservation.id),
        "status": reservation.status,
    }


async def create_reservation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    payload: ReservationCreate,
    idempotency_key: str,
    user: User | None = None,
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
        payload: Validated reservation request body.
        idempotency_key: Client-supplied idempotency token.
        user: Optional signed-in account. When provided, the
            reservation is linked to the account (``user_id``) and
            the duplicate-active check uses ``user.email`` instead of
            the request body.

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
    replay = await _replay_idempotent(session, workshop_id, idempotency_key)
    if replay is not None:
        return replay, True

    try:
        workshop = await _lock_workshop(session, workshop_id)

        replay = await _replay_idempotent(session, workshop_id, idempotency_key)
        if replay is not None:
            await session.rollback()
            return replay, True

        attendee_email = user.email if user else payload.attendee_email
        await _reject_duplicate_attendee(session, workshop_id, attendee_email)

        active_count = await count_active_reservations(session, workshop_id)
        if active_count >= workshop.max_capacity:
            raise WorkshopFullError(str(workshop_id))

        reservation = _new_reservation(workshop_id, payload, user, attendee_email)
        await _insert_reservation(session, reservation, workshop_id)
        await _persist_idempotency_key(session, idempotency_key, workshop_id, reservation.id)

        try:
            await session.commit()
        except IntegrityError:
            # Two requests raced past the replay check with the same
            # idempotency key. The other one committed first; re-read
            # and return the winner's reservation.
            await session.rollback()
            replay = await _replay_idempotent(session, workshop_id, idempotency_key)
            if replay is not None:
                return replay, True
            raise
    except (AlreadyReservedError, WorkshopFullError, WorkshopNotFoundError):
        # Release the workshop row lock before surfacing a rejected request.
        await session.rollback()
        raise

    await session.refresh(reservation)
    await _broadcast_creation(workshop, reservation, active_count)
    logger.info(
        "reservation.created",
        workshop_id=str(workshop_id),
        reservation_id=str(reservation.id),
        user_id=str(user.id) if user else None,
    )
    return ReservationResponse.model_validate(reservation), False


async def _replay_idempotent(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    idempotency_key: str,
) -> ReservationResponse | None:
    """Return the prior reservation for this key+workshop, if any."""
    existing = await find_idempotent_reservation(session, workshop_id, idempotency_key)
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
    payload: ReservationCreate,
    user: User | None,
    attendee_email: str,
) -> Reservation:
    """Build a new in-memory ``Reservation`` ORM object."""
    return Reservation(
        workshop_id=workshop_id,
        attendee_name=user.full_name if user else payload.attendee_name,
        attendee_email=attendee_email,
        user_id=user.id if user else None,
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
    reservation_id: uuid.UUID,
) -> None:
    """Stage the idempotency record. A duplicate key race surfaces as
    a re-read on the caller side via the IntegrityError re-raised
    below; the reservation row is not visible until commit, so the
    helper cannot itself detect the winner and leaves the decision
    to the orchestrator.
    """
    session.add(
        IdempotencyKey(
            key=idempotency_key,
            workshop_id=workshop_id,
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

    Authorization: a valid signed-in account is **required**; the
    reservation must be owned by that account (or be an anonymous
    legacy reservation, to preserve the historical "cancel a
    legacy booking" path). Anonymous callers and non-owners both
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
    # the database enforces the same rule even if the in-memory
    # check above is ever bypassed.
    stmt = (
        update(Reservation)
        .where(Reservation.id == reservation_id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        .where((Reservation.user_id == user.id) | (Reservation.user_id.is_(None)))
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
    await _broadcast_cancellation(session, workshop_id, cancelled)
    logger.info(
        "reservation.cancelled",
        workshop_id=str(workshop_id),
        reservation_id=str(cancelled.id),
        user_id=str(user.id) if user else None,
    )
    return ReservationCancelResponse.model_validate(cancelled)


def _is_authorized_to_cancel(reservation: Reservation, user: User) -> bool:
    """Return True if ``user`` is allowed to cancel ``reservation``.

    Authenticated callers may cancel only their own reservations
    and anonymous legacy reservations. This prevents a logged-in
    user from cancelling someone else's booking by guessing UUIDs.
    The caller guarantees ``user is not None``; anonymous access
    is rejected earlier in :func:`cancel_reservation`.

    Args:
        reservation: The reservation being cancelled.
        user: The signed-in account. Must not be ``None``.

    Returns:
        True when the cancellation is permitted.
    """
    return reservation.user_id is None or reservation.user_id == user.id


async def _broadcast_cancellation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    cancelled: Reservation,
) -> None:
    """Publish a ``reservation_cancelled`` event with current spot count.

    Recomputes the active reservation count post-cancel so subscribers
    see the new available total without polling the detail endpoint.

    Args:
        session: Active async database session.
        workshop_id: Workshop whose count changed.
        cancelled: The just-cancelled row (used for the event payload).
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
            "reservation_id": str(cancelled.id),
        },
    )
