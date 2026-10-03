"""Write-side service functions for the reservation surface.

The public coroutines here own the **only** mutations to the
``reservations``, ``idempotency_keys`` and ``waitlist_entries``
tables. They are the place where the central invariants of the
system are enforced:

* **Capacity is enforced by a row lock** on the parent workshop
  (``SELECT ... FOR UPDATE``) inside the create transaction. See
  :func:`create_reservation` for the exact sequence.
* **The registration window is enforced** before any seat is
  reserved: a workshop that has started, or whose explicit
  registration deadline has passed, refuses new bookings with
  ``RegistrationClosedError`` (plan 0.1). Draft and cancelled
  workshops are 404-shaped so their existence is not leaked.
* **Idempotency is enforced by the ``idempotency_keys`` table**
  and by the partial unique index ``uq_active_reservation`` on
  ``(workshop_id, attendee_email) WHERE status='active'``. The
  partial index is a backstop: the key check is the first line.
* **A duplicate-attendee race maps to ``AlreadyReservedError``**
  (plan 0.2). The partial index is keyed on the attendee email,
  so tripping it means "this person already holds a seat", not
  "the workshop is full".
* **Cancellation is idempotent.** A second ``DELETE`` returns 200
  with the existing cancelled row instead of 409.
* **Waitlist promotion is exactly-once** because it runs inside
  the cancelling transaction (plan 1.2): the seat release and the
  promotion commit or roll back together.
* **Ownership is strict.** Every reservation has a non-null
  ``user_id`` (create requires authentication) and a cancel is
  refused unless that ``user_id`` matches the caller.

Every successful mutation publishes a domain event on the
app event bus (``src.events``); the realtime projector
broadcasts it so connected browsers see live updates.
Those events carry counts only: the channel is
unauthenticated, so no reservation identifier, email,
or booking code is ever broadcast on it.
"""

import secrets
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth.models import User
from ws_core.errors import (
    AlreadyReservedError,
    RegistrationClosedError,
    ReservationNotFoundError,
    WaitlistEntryNotFoundError,
    WorkshopFullError,
    WorkshopNotFoundError,
)

from src.events import (
    ReservationCancelled,
    ReservationCreated,
    WaitlistCancelled,
    WaitlistJoined,
    WaitlistLeft,
    WaitlistPromoted,
    get_event_bus,
)
from src.models.idempotency_key import IdempotencyKey
from src.models.reservation import (
    RESERVATION_STATUS_ACTIVE,
    RESERVATION_STATUS_CANCELLED,
    Reservation,
)
from src.models.waitlist_entry import (
    WAITLIST_STATUS_ACTIVE,
    WAITLIST_STATUS_CANCELLED,
    WAITLIST_STATUS_PROMOTED,
    WaitlistEntry,
)
from src.models.workshop import (
    WORKSHOP_STATUS_PUBLISHED,
    Workshop,
)
from src.schemas.reservation import (
    ReservationCancelResponse,
    ReservationCreate,
    ReservationResponse,
)
from src.services.reservation_queries import (
    count_active_reservations,
    find_active_waitlist_entry,
    find_idempotent_reservation,
)

logger = structlog.get_logger(__name__)

# Booking-code alphabet: no 0/O, 1/I/L confusables so the code
# survives being read aloud or scanned (plan 1.4).
_BOOKING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_BOOKING_CODE_LENGTH = 6
_BOOKING_CODE_MAX_ATTEMPTS = 5


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC so window comparisons are sound."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _generate_booking_code() -> str:
    """Return a fresh ``WKS-XXXXXX`` confirmation code."""
    return "WKS-" + "".join(
        secrets.choice(_BOOKING_CODE_ALPHABET) for _ in range(_BOOKING_CODE_LENGTH)
    )


def _constraint_name(exc: IntegrityError) -> str:
    """Extract the violated constraint name from an ``IntegrityError``.

    asyncpg exposes the failing constraint as ``constraint_name`` on
    the original driver exception; SQLAlchemy wraps it as
    ``exc.orig``. Unknown drivers yield an empty string, which the
    callers treat as "not one of the constraints we handle".
    """
    orig = getattr(exc, "orig", None)
    return str(getattr(orig, "constraint_name", "") or "")


def _reservation_to_dict(reservation: Reservation) -> dict:
    """Build the per-reservation portion of the SSE broadcast payload.

    The broadcast channel is **public**: ``GET /api/workshops/events``
    requires no credentials, so every anonymous visitor sees every
    event. Only the reservation's ``status`` is emitted.

    The reservation ``id``, attendee identity and booking code are
    deliberately excluded. An id is a capability: it is the only
    value a caller needs to act on a reservation row, and publishing
    it on an unauthenticated channel handed every logged-out visitor
    a ready-made list of ids to cancel. Clients that legitimately
    hold a reservation obtain its id from ``GET /api/reservations/me``
    or the authenticated workshop detail, both of which are scoped to
    the caller.

    Args:
        reservation: The reservation being reported.

    Returns:
        A dict with primitive types so it survives ``json.dumps``.
    """
    return {"status": reservation.status}


def _require_published(workshop: Workshop) -> None:
    """Raise 404 unless the workshop is published.

    Draft and cancelled workshops are surfaced as
    ``WorkshopNotFoundError`` so the API does not leak the existence
    of unpublished sessions - consistent with the codebase's
    404-instead-of-403 error philosophy.
    """
    if workshop.status != WORKSHOP_STATUS_PUBLISHED:
        raise WorkshopNotFoundError(str(workshop.id))


def _require_registration_open(workshop: Workshop) -> None:
    """Raise 409 when the registration window has closed.

    A workshop that has already started is rejected with reason
    ``"started"``; one whose explicit ``registration_closes_at``
    (which defaults to ``starts_at`` when unset) has passed is
    rejected with reason ``"closed"``.
    """
    now = datetime.now(UTC)
    if now >= _as_utc(workshop.starts_at):
        raise RegistrationClosedError(str(workshop.id), reason="started")
    closes_at = workshop.registration_closes_at
    if closes_at is not None and now >= _as_utc(closes_at):
        raise RegistrationClosedError(str(workshop.id), reason="closed")


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
        4. Enforce the lifecycle: the workshop must be published
           (404 otherwise) and its registration window open
           (409 otherwise).
        5. Refuse if the attendee already has an active booking.
        6. Refuse if the workshop is at capacity.
        7. Insert the reservation (with booking-code collision
           retry) and the idempotency record.
        8. Broadcast a ``reservation_created`` event.

    Steps 1-7 live in dedicated helpers below so the top-level
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
        WorkshopNotFoundError: If the workshop does not exist or is
            not published.
        RegistrationClosedError: If the workshop has started or its
            registration deadline has passed.
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

        # Lifecycle gates: unpublished/cancelled workshops are 404, and a
        # closed registration window is a 409. Both run under the lock so
        # a concurrent publish/cancel cannot race a booking through.
        _require_published(workshop)
        _require_registration_open(workshop)

        # Ensure the attendee doesn't already have an active booking for this workshop
        attendee_email = user.email
        await _reject_duplicate_attendee(session, workshop_id, attendee_email)

        # Verify remaining capacity before admitting the new reservation
        active_count = await count_active_reservations(session, workshop_id)
        if active_count >= workshop.max_capacity:
            raise WorkshopFullError(str(workshop_id))

        # Create and stage the new reservation and its idempotency record
        reservation = await _insert_reservation(session, workshop_id, user, attendee_email)
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
    except (
        AlreadyReservedError,
        WorkshopFullError,
        WorkshopNotFoundError,
        RegistrationClosedError,
    ):
        # Always release the database row lock on expected validation errors
        await session.rollback()
        raise

    # Step 8: Refresh model attributes from the database and broadcast real-time event
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
    supplied value. A fresh booking code is generated here; the
    unique index on ``booking_code`` is the collision backstop
    handled by :func:`_insert_reservation`.
    """
    return Reservation(
        workshop_id=workshop_id,
        attendee_name=user.full_name,
        attendee_email=attendee_email,
        user_id=user.id,
        booking_code=_generate_booking_code(),
        status=RESERVATION_STATUS_ACTIVE,
    )


async def _insert_reservation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user: User,
    attendee_email: str,
) -> Reservation:
    """Build, stage, and flush a reservation, retrying booking-code collisions.

    Two integrity failures are expected and handled:

    * **Duplicate attendee** - the partial unique index
      ``uq_active_reservation`` trips when a racing request booked the
      same email between our pre-check and our flush. Because the index
      is keyed on ``(workshop_id, attendee_email) WHERE status='active'``,
      the semantic is "this person already holds a seat", so the failure
      is surfaced as ``AlreadyReservedError`` (plan 0.2), **not**
      ``WorkshopFullError``.
    * **Booking-code collision** - the unique index on ``booking_code``
      trips on the astronomically rare chance that a generated code
      already exists. The insert is retried inside a savepoint with a
      fresh code; the savepoint keeps the surrounding transaction (and
      the workshop row lock) intact.

    Args:
        session: Active async database session.
        workshop_id: Target workshop (used for error context).
        user: Owning account; supplies the attendee identity.
        attendee_email: Normalized attendee email.

    Returns:
        The flushed ``Reservation`` with its generated id and booking code.

    Raises:
        AlreadyReservedError: If the attendee already holds a seat.
        WorkshopFullError: If the booking-code space is exhausted
            (practically unreachable).
    """
    for _attempt in range(_BOOKING_CODE_MAX_ATTEMPTS):
        reservation = _new_reservation(workshop_id, user, attendee_email)
        try:
            # A savepoint lets us retry the booking-code collision
            # without rolling back the outer transaction (which would
            # release the workshop row lock acquired by the caller).
            async with session.begin_nested():
                session.add(reservation)
                await session.flush()
            return reservation
        except IntegrityError as exc:
            constraint = _constraint_name(exc)
            if constraint == "uq_active_reservation":
                raise AlreadyReservedError(str(workshop_id)) from exc
            if constraint == "uq_reservations_booking_code":
                # Collision: the savepoint rollback already undid the
                # insert, so the next loop builds a fresh reservation
                # with a new code.
                logger.debug("reservation.booking_code_collision", workshop_id=str(workshop_id))
                continue
            raise
    # The booking-code space is ~887 million; reaching this branch is
    # practically impossible, but fail closed rather than loop forever.
    raise WorkshopFullError(str(workshop_id))


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
    """Publish the ``reservation_created`` domain event.

    ``active_count_before_insert`` is the active count observed
    while holding the row lock; the just-inserted reservation is
    not included in that count, so the post-insert available is
    ``max_capacity - (active_count + 1)`` clamped to zero.
    """
    await get_event_bus().publish(
        ReservationCreated(
            workshop_id=reservation.workshop_id,
            available_spots=max(
                workshop.max_capacity - active_count_before_insert - 1,
                0,
            ),
            reservation=_reservation_to_dict(reservation),
        )
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

    After the seat is released, the oldest eligible waitlist entry is
    promoted **inside the same transaction** (plan 1.2), so the
    release and the promotion are exactly-once: they commit or roll
    back together. A promotion publishes a ``waitlist_promoted``
    event carrying counts only.

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
    # the database enforces the same strict-ownership rule even if
    # the in-memory check above is ever bypassed.
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

    # Re-read the parent workshop (still inside the transaction) so the
    # promotion can check its lifecycle and capacity.
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id))
    ).scalar_one()

    # Spot count with the seat released but before any promotion. This
    # is the value broadcast on the ``reservation_cancelled`` event.
    count_after_cancel = await count_active_reservations(session, workshop_id)
    spots_after_cancel = max(workshop.max_capacity - count_after_cancel, 0)

    # Promote the next waitlisted attendee inside this transaction so the
    # seat release and the new booking commit together (exactly-once).
    promoted: Reservation | None = None
    if workshop.status == WORKSHOP_STATUS_PUBLISHED:
        promoted = await _promote_next_waitlist_entry(session, workshop)

    # Spot count after any promotion. When a promotion happened this is
    # the value broadcast on the ``waitlist_promoted`` event; when none
    # did, the seat stays open and this equals ``spots_after_cancel``.
    count_after_promotion = await count_active_reservations(session, workshop_id)
    spots_after_promotion = max(workshop.max_capacity - count_after_promotion, 0)

    await session.commit()

    await get_event_bus().publish(
        ReservationCancelled(
            workshop_id=workshop_id,
            available_spots=spots_after_cancel,
        )
    )
    if promoted is not None:
        await get_event_bus().publish(
            WaitlistPromoted(
                workshop_id=workshop_id,
                available_spots=spots_after_promotion,
            )
        )
    logger.info(
        "reservation.cancelled",
        workshop_id=str(workshop_id),
        reservation_id=str(cancelled.id),
        user_id=str(user.id) if user else None,
        promoted_reservation_id=str(promoted.id) if promoted else None,
    )
    return ReservationCancelResponse.model_validate(cancelled)


async def _promote_next_waitlist_entry(
    session: AsyncSession,
    workshop: Workshop,
) -> Reservation | None:
    """Promote the oldest eligible waitlist entry into a reservation.

    Runs inside the caller's transaction. The oldest ``active`` entry
    is locked with ``SELECT ... FOR UPDATE`` ordered by ``created_at``,
    so two concurrent cancels cannot promote the same entry: the
    second transaction blocks, then re-evaluates and picks the next
    entry once the first has committed (Postgres re-checks the
    ``status = 'active'`` predicate after the lock is released).

    Ineligible entries are skipped and marked ``cancelled``:

    * the owning account was deleted (the ``user_id`` FK is
      ``ON DELETE CASCADE``, so this is defensive), or
    * the user already holds an active seat (they booked directly
      after joining the waitlist).

    Promotion is also skipped when the workshop has no open seat,
    which guards against a capacity reduction after entries were
    queued.

    Args:
        session: Active async database session (the caller's transaction).
        workshop: The workshop whose seat was just released.

    Returns:
        The newly created ``Reservation``, or ``None`` when no eligible
        entry exists.
    """
    while True:
        entry = (
            await session.execute(
                select(WaitlistEntry)
                .where(WaitlistEntry.workshop_id == workshop.id)
                .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
                .order_by(WaitlistEntry.created_at.asc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if entry is None:
            return None

        user = (
            await session.execute(select(User).where(User.id == entry.user_id))
        ).scalar_one_or_none()
        if user is None:
            # Account deleted: skip to the next entry.
            entry.status = WAITLIST_STATUS_CANCELLED
            continue

        # Skip users who already hold an active seat on this workshop.
        already_booked = (
            await session.execute(
                select(Reservation)
                .where(Reservation.workshop_id == workshop.id)
                .where(Reservation.user_id == user.id)
                .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
            )
        ).scalar_one_or_none()
        if already_booked is not None:
            entry.status = WAITLIST_STATUS_CANCELLED
            continue

        # Never overbook: only promote into a genuinely open seat.
        active_count = await count_active_reservations(session, workshop.id)
        if active_count >= workshop.max_capacity:
            return None

        try:
            reservation = await _insert_reservation(
                session, workshop.id, user, user.email
            )
        except AlreadyReservedError:
            # Lost a race to a direct booking: skip this entry.
            entry.status = WAITLIST_STATUS_CANCELLED
            continue

        entry.status = WAITLIST_STATUS_PROMOTED
        entry.promoted_at = func.now()
        entry.reservation_id = reservation.id
        return reservation


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


async def get_reservation(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    user: User,
) -> Reservation:
    """Return a reservation owned by ``user``.

    Owner-only read (plan 1.4): the ticket page fetches the
    caller's own reservation - including its booking code -
    by id. A reservation owned by another account, or an
    unknown id, raises ``ReservationNotFoundError`` so the
    API does not leak which reservations exist.

    Args:
        session: Active async database session.
        reservation_id: Reservation to fetch.
        user: The signed-in account that must own it.

    Returns:
        The ``Reservation`` ORM row.

    Raises:
        ReservationNotFoundError: If the reservation does not
            exist or is owned by another account.
    """
    reservation = (
        await session.execute(
            select(Reservation).where(Reservation.id == reservation_id)
        )
    ).scalar_one_or_none()
    if reservation is None or reservation.user_id != user.id:
        raise ReservationNotFoundError(str(reservation_id))
    return reservation


async def join_waitlist(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user: User,
) -> tuple[WaitlistEntry, bool]:
    """Add the caller to a workshop's waitlist.

    The workshop must be published (404 otherwise) and its
    registration window open (409 otherwise). A caller who already
    holds an active reservation is rejected with
    ``AlreadyReservedError`` - there is nothing to wait for. Joining
    is idempotent: a caller who already holds an active waitlist
    entry gets that entry back with ``replayed=True``.

    Args:
        session: Active async database session.
        workshop_id: Workshop to join the waitlist for.
        user: The signed-in account joining.

    Returns:
        Tuple of ``(waitlist entry, replayed flag)``.

    Raises:
        WorkshopNotFoundError: If the workshop does not exist or is
            not published.
        RegistrationClosedError: If the registration window closed.
        AlreadyReservedError: If the caller already holds a seat.
    """
    try:
        workshop = await _lock_workshop(session, workshop_id)
        _require_published(workshop)
        _require_registration_open(workshop)

        # Already booked? There is nothing to waitlist for.
        already_booked = (
            await session.execute(
                select(Reservation)
                .where(Reservation.workshop_id == workshop_id)
                .where(Reservation.user_id == user.id)
                .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
            )
        ).scalar_one_or_none()
        if already_booked is not None:
            raise AlreadyReservedError(str(workshop_id))

        # Idempotent re-join: return the existing active entry.
        existing = await find_active_waitlist_entry(session, workshop_id, user.id)
        if existing is not None:
            await session.rollback()
            return existing, True

        entry = WaitlistEntry(
            workshop_id=workshop_id,
            user_id=user.id,
            status=WAITLIST_STATUS_ACTIVE,
        )
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError as exc:
            # The partial unique index caught a concurrent join by the
            # same user. Roll back and replay their existing entry.
            await session.rollback()
            if _constraint_name(exc) == "uq_active_waitlist_entry":
                replay = await find_active_waitlist_entry(session, workshop_id, user.id)
                if replay is not None:
                    return replay, True
            raise
        await session.commit()
        await _broadcast_waitlist_join(workshop)
        logger.info("waitlist.joined", workshop_id=str(workshop_id), user_id=str(user.id))
        return entry, False
    except (
        AlreadyReservedError,
        WorkshopNotFoundError,
        RegistrationClosedError,
    ):
        await session.rollback()
        raise


async def leave_waitlist(
    session: AsyncSession,
    entry_id: uuid.UUID,
    user: User,
) -> WaitlistEntry:
    """Remove the caller from a waitlist.

    Only the entry's owner may leave it; a foreign or unknown id is a
    404 so the API does not leak which waitlist entries exist. Leaving
    an already-cancelled or promoted entry is a no-op that returns the
    row as-is.

    Args:
        session: Active async database session.
        entry_id: Waitlist entry to leave.
        user: The signed-in account that owns the entry.

    Returns:
        The (now cancelled) ``WaitlistEntry``.

    Raises:
        WaitlistEntryNotFoundError: If the entry does not exist or is
            owned by another account.
    """
    entry = (
        await session.execute(select(WaitlistEntry).where(WaitlistEntry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None or entry.user_id != user.id:
        raise WaitlistEntryNotFoundError(str(entry_id))

    if entry.status == WAITLIST_STATUS_ACTIVE:
        entry.status = WAITLIST_STATUS_CANCELLED
        await session.commit()
        await _broadcast_waitlist_leave(entry.workshop_id)
        logger.info("waitlist.left", waitlist_entry_id=str(entry_id))
    return entry


async def _broadcast_waitlist_join(workshop: Workshop) -> None:
    """Publish a ``waitlist_joined`` event with the current queue depth."""
    await get_event_bus().publish(
        WaitlistJoined(workshop_id=workshop.id),
    )


async def _broadcast_waitlist_leave(workshop_id: uuid.UUID) -> None:
    """Publish a ``waitlist_left`` event."""
    await get_event_bus().publish(
        WaitlistLeft(workshop_id=workshop_id),
    )


async def cancel_waitlist_for_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> int:
    """Cancel every active waitlist entry for a workshop.

    Called when a workshop is deleted or cancelled so no orphaned
    places in line remain. Returns the number of entries cancelled.
    """
    result = await session.execute(
        update(WaitlistEntry)
        .where(WaitlistEntry.workshop_id == workshop_id)
        .where(WaitlistEntry.status == WAITLIST_STATUS_ACTIVE)
        .values(status=WAITLIST_STATUS_CANCELLED)
    )
    count = result.rowcount or 0
    if count:
        await get_event_bus().publish(
            WaitlistCancelled(workshop_id=workshop_id),
        )
    return int(count)
