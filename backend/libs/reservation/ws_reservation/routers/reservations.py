"""Reservation creation, cancellation, and account history endpoints.

Routes are mounted at:

* ``POST   /workshops/{workshop_id}/reservations`` : create.
* ``GET    /reservations/{reservation_id}``        : fetch one
                                                     (owner only).
* ``DELETE /reservations/{reservation_id}``        : cancel.
* ``GET    /reservations/me``      : list the signed-in
                                     account's reservations
                                     with workshop titles.
* ``GET    /waitlist/me``          : list the signed-in
                                     account's active
                                     waitlist entries.
* ``DELETE /waitlist/{waitlist_entry_id}`` : leave a waitlist.

The router is built by :func:`create_router`, which closes
over the dependency :class:`~ws_core.container.Container`;
the mutation handlers pass the container's event bus to
the publishing service calls.

Authorization rules
-------------------

* ``create`` requires a valid signed-in account
  (``get_current_user``). The reservation is always linked to that
  account and its ``user_id`` is never null. Accepting anonymous
  creators produced unowned rows that no account could later prove
  ownership of, and which the cancel path used to let any signed-in
  account cancel.
* ``cancel`` requires a valid signed-in account
  (``get_current_user``); it succeeds only when the reservation's
  ``user_id`` matches the caller. Unauthenticated callers are
  rejected with 401, and a caller trying to cancel a reservation they
  do not own gets a 404 (no information leak).
* ``get`` (``GET /reservations/{id}``) is owner-only: a signed-in
  caller may read only their own reservation; any other id is a 404.
* ``me`` requires a valid token (``get_current_user``).
* ``leave waitlist`` requires a valid token and ownership of the
  waitlist entry; a foreign or unknown id is a 404.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import get_current_user
from ws_core.auth.models import User
from ws_core.container import Container
from ws_core.db.engine import get_db

from ws_reservation.schemas.reservation import (
    MyReservationResponse,
    ReservationCancelResponse,
    ReservationCreate,
    ReservationResponse,
)
from ws_reservation.schemas.waitlist import (
    MyWaitlistEntryResponse,
    WaitlistEntryResponse,
)
from ws_reservation.services import reservation_queries, reservation_service


def create_router(container: Container) -> APIRouter:
    """Build the reservation router bound to ``container``.

    Args:
        container: The dependencies the handlers need; the
            container's event bus is passed to every
            publishing service call.

    Returns:
        The configured ``APIRouter``.
    """
    router = APIRouter(tags=["reservations"])

    @router.get("/reservations/me", response_model=list[MyReservationResponse])
    async def my_reservations(
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> list[MyReservationResponse]:
        """List the signed-in account's reservations, newest first.

        Each row carries the parent workshop's title so the dashboard
        can render without a second round-trip per row.

        Args:
            user: The signed-in account.
            session: Active async database session.

        Returns:
            A list of ``MyReservationResponse`` ordered by
            ``created_at`` descending.
        """
        rows = await reservation_queries.list_user_reservations(session, user)
        return [
            MyReservationResponse.model_validate(
                {
                    **ReservationResponse.model_validate(reservation).model_dump(),
                    "workshop_title": title,
                }
            )
            for reservation, title in rows
        ]

    @router.get("/waitlist/me", response_model=list[MyWaitlistEntryResponse])
    async def my_waitlist_entries(
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> list[MyWaitlistEntryResponse]:
        """List the signed-in account's active waitlist entries.

        Each row carries the parent workshop's title so the
        account view can render a human-friendly label without
        a second round-trip per row, plus the caller's 1-based
        position in that workshop's queue. Only active places
        in line are returned; promoted and cancelled entries
        are historical.

        Args:
            user: The signed-in account.
            session: Active async database session.

        Returns:
            A list of ``MyWaitlistEntryResponse`` ordered by
            ``created_at`` ascending (oldest place in line
            first).
        """
        rows = await reservation_queries.list_user_waitlist_entries(session, user)
        entries: list[MyWaitlistEntryResponse] = []
        for entry, title in rows:
            position = await reservation_queries.get_waitlist_position(
                session, entry.workshop_id, user.id
            )
            if position is None:
                # The entry was promoted or cancelled between the
                # two queries; it is no longer an active place in line.
                continue
            entries.append(
                MyWaitlistEntryResponse.model_validate(
                    {
                        **WaitlistEntryResponse.model_validate(entry).model_dump(),
                        "workshop_title": title,
                        "position": position,
                    }
                )
            )
        return entries

    @router.get(
        "/reservations/{reservation_id}",
        response_model=ReservationResponse,
        responses={
            401: {"description": "Authentication required"},
            404: {"description": "Reservation not found or not owned by you"},
        },
    )
    async def get_reservation(
        reservation_id: Annotated[uuid.UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> ReservationResponse:
        """Fetch a single reservation by id (owner only).

        This backs the ticket page (plan 1.4): the signed-in caller
        reads their own reservation - including its booking code - to
        render the QR ticket. A reservation owned by another account,
        or an unknown id, is a 404 so the API does not leak which
        reservations exist.

        Args:
            reservation_id: Reservation UUID from the URL.
            session: Active async database session.
            user: The signed-in account.

        Returns:
            The caller's ``ReservationResponse``.

        Raises:
            ReservationNotFoundError: 404 if the reservation does not
                exist or is owned by another account.
        """
        reservation = await reservation_service.get_reservation(session, reservation_id, user)
        return ReservationResponse.model_validate(reservation)

    @router.post(
        "/workshops/{workshop_id}/reservations",
        response_model=ReservationResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            200: {"description": "Idempotent replay of a prior successful reservation"},
            401: {"description": "Authentication required"},
            404: {"description": "Workshop not found"},
            409: {"description": "Workshop is full, already reserved, or registration closed"},
        },
    )
    async def create_reservation(
        response: Response,
        workshop_id: Annotated[uuid.UUID, Path()],
        payload: ReservationCreate,
        session: Annotated[AsyncSession, Depends(get_db)],
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=255,
                description=(
                    "Client-supplied token; same value on retry replays the original reservation."
                ),
            ),
        ],
        user: Annotated[User, Depends(get_current_user)],
    ) -> ReservationResponse:
        """Reserve a seat on a workshop.

        Requires a signed-in account and an ``Idempotency-Key`` header. A
        retried request with the same key and same workshop returns the
        original reservation with HTTP 200; a fresh request returns 201. A
        key only ever replays the reservation created by the **same**
        account, so reusing somebody else's key cannot disclose their
        booking.

        The body still carries ``attendee_name`` / ``attendee_email`` for
        wire compatibility, but the authenticated account is authoritative:
        those values are ignored and the reservation is stored against the
        account's own name and email.

        The header is validated to be 1..255 characters so we do
        not silently accept a missing, empty, or absurdly large key.
        The service layer would otherwise treat each oversized value
        as a brand-new key and risk polluting the idempotency table
        (or running the DB out of space).

        Args:
            response: FastAPI response object, mutated to 200 on replay.
            workshop_id: Workshop UUID from the URL.
            payload: Validated reservation body (name, email); ignored
                in favour of the authenticated account.
            session: Active async database session.
            idempotency_key: Client-supplied token; same value on retry
                replays the original reservation.
            user: The signed-in account that will own the reservation.

        Returns:
            The created (or replayed) reservation.

        Raises:
            WorkshopNotFoundError: 404.
            RegistrationClosedError: 409 if the workshop has started or
                its registration deadline has passed.
            WorkshopFullError: 409 if the workshop is at capacity.
            AlreadyReservedError: 409 if the attendee already has an
                active booking for this workshop.
        """
        reservation, replayed = await reservation_service.create_reservation(
            session,
            workshop_id,
            payload,
            idempotency_key,
            user,
            event_bus=container.event_bus,
        )
        if replayed:
            response.status_code = status.HTTP_200_OK
        return reservation

    @router.delete(
        "/reservations/{reservation_id}",
        response_model=ReservationCancelResponse,
        responses={
            401: {"description": "Authentication required"},
            404: {"description": "Reservation not found"},
        },
    )
    async def cancel_reservation(
        reservation_id: Annotated[uuid.UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> ReservationCancelResponse:
        """Cancel a reservation. Idempotent on a second call.

        Requires a valid bearer token. The call only succeeds when the
        reservation is owned by the signed-in account; otherwise a 404 is
        returned to avoid leaking the existence of someone else's booking.
        There is no anonymous or "legacy" cancellation path: every
        reservation created today is owned, and ownership is the sole
        authorization for a cancel.

        A second ``DELETE`` on the same id is a no-op and returns 200
        with the existing ``cancelled_at``. This makes client retries
        safe. Cancelling releases the seat and, when a waitlist exists,
        promotes the oldest waitlisted attendee inside the same
        transaction.

        Args:
            reservation_id: Reservation UUID from the URL.
            session: Active async database session.
            user: The signed-in account (required).

        Returns:
            The cancelled (or already-cancelled) reservation as a
            ``ReservationCancelResponse``.

        Raises:
            HTTPException: 401 if the bearer token is missing or invalid.
            ReservationNotFoundError: 404 if the reservation does not
                exist or the caller is not authorized to cancel it.
        """
        return await reservation_service.cancel_reservation(
            session, reservation_id, user, event_bus=container.event_bus
        )

    @router.delete(
        "/waitlist/{waitlist_entry_id}",
        response_model=WaitlistEntryResponse,
        responses={
            401: {"description": "Authentication required"},
            404: {"description": "Waitlist entry not found"},
        },
    )
    async def leave_waitlist(
        waitlist_entry_id: Annotated[uuid.UUID, Path()],
        session: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> WaitlistEntryResponse:
        """Leave a waitlist.

        Requires a valid bearer token and ownership of the waitlist
        entry. A foreign or unknown id is a 404 so the API does not
        leak which waitlist entries exist. Leaving an already-cancelled
        or promoted entry is a no-op that returns the row as-is.

        Args:
            waitlist_entry_id: Waitlist entry UUID from the URL.
            session: Active async database session.
            user: The signed-in account that owns the entry.

        Returns:
            The (now cancelled) ``WaitlistEntryResponse``.

        Raises:
            WaitlistEntryNotFoundError: 404 if the entry does not exist
                or is owned by another account.
        """
        entry = await reservation_service.leave_waitlist(
            session, waitlist_entry_id, user, event_bus=container.event_bus
        )
        return WaitlistEntryResponse.model_validate(entry)

    return router
