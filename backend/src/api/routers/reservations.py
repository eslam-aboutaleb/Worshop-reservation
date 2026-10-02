"""Reservation creation, cancellation, and account history endpoints.

Routes are mounted at:

* ``POST   /workshops/{workshop_id}/reservations`` : create.
* ``DELETE /reservations/{reservation_id}``        : cancel.
* ``GET    /reservations/me``                      : list the signed-in
  account's reservations with workshop titles.

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
* ``me`` requires a valid token (``get_current_user``).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import get_current_user
from src.configuration.database import get_db
from src.models.user import User
from src.schemas.reservation import (
    MyReservationResponse,
    ReservationCancelResponse,
    ReservationCreate,
    ReservationResponse,
)
from src.services import reservation_queries, reservation_service

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


@router.post(
    "/workshops/{workshop_id}/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Idempotent replay of a prior successful reservation"},
        401: {"description": "Authentication required"},
        404: {"description": "Workshop not found"},
        409: {"description": "Workshop is full or already reserved by the attendee"},
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
    original reservation with HTTP 200; a fresh request returns 201.
    A key only ever replays the reservation created by the **same**
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
        WorkshopFullError: 409 if the workshop is at capacity.
        AlreadyReservedError: 409 if the attendee already has an
            active booking for this workshop.
    """
    reservation, replayed = await reservation_service.create_reservation(
        session, workshop_id, payload, idempotency_key, user
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
    reservation is owned by the signed-in account (or is an
    anonymous legacy reservation); otherwise a 404 is returned to
    avoid leaking the existence of someone else's booking.

    A second ``DELETE`` on the same id is a no-op and returns 200
    with the existing ``cancelled_at``. This makes client retries
    safe.

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
    return await reservation_service.cancel_reservation(session, reservation_id, user)
