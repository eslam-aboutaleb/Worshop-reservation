"""Workshop browsing, discovery, and lifecycle endpoints.

Routes under the ``/workshops`` prefix:

* ``GET  /``                 - paginated, filtered catalogue.
* ``POST /``                 - create a session (admin or
                                organization member).
* ``GET  /events``           - single SSE stream of events.
* ``GET  /{workshop_id}``    - detail (own reservations only).
* ``PUT  /{workshop_id}``    - edit a session (admin or
                                organization member).
* ``POST /{workshop_id}/publish`` - publish a draft (admin or
                                organization member).
* ``POST /{workshop_id}/cancel``  - cancel a session (admin or
                                organization member).
* ``POST /{workshop_id}/waitlist`` - join the waitlist (user).
* ``POST /{workshop_id}/reviews``  - review a past session
                                (past attendee).
* ``DELETE /{workshop_id}``  - delete an unbooked session (admin
                                or organization member).

Mutation authorization (roadmap 2.1)
--------------------------------------

The five mutation routes use the ``get_current_organizer``
dependency instead of ``get_current_admin``. A caller may
mutate workshops when they are the configured super-admin,
hold the ``organizer``/``admin`` platform role, or are a
member of the organization that owns the target workshop.
A caller who is not an organizer at all is rejected with
403 (the pre-organizer behavior); a caller who is an
organizer but may not see the target workshop gets a 404
so foreign workshops are not leaked.

SSE stream
----------

The single ``/events`` endpoint is the only SSE stream exposed. It
emits one event per reservation or workshop mutation, with a
``workshop_id`` field the client can filter on. We use one channel
rather than per-workshop channels because (a) the filter is trivial
on the client and (b) it lets a single connection power both the
list view and any open detail view.

The channel is public and carries **counts only** - never
reservation ids, attendee emails, or booking codes.

Reviews (roadmap 2.4)
-------------------------

``POST /{workshop_id}/reviews`` is open to any signed-in
account, but the service layer only accepts it from callers
who held a reservation for the workshop (any status) and
whose session has ended. Rating bounds are validated at the
schema layer (422); eligibility and one-review-per-user are
409s with the stable codes ``review_not_eligible`` and
``review_already_exists``.
"""

import asyncio
import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import get_current_user, get_optional_user, is_admin
from ws_core.auth.models import User
from ws_core.db.engine import get_db
from ws_core.realtime import format_sse, subscribe_global, unsubscribe_global

from src.auth import get_current_organizer
from src.configuration.settings import get_settings
from src.schemas.review import ReviewCreate, ReviewResponse
from src.schemas.workshop import (
    WaitlistEntryResponse,
    WaitlistJoinResponse,
    WorkshopCreate,
    WorkshopDetailResponse,
    WorkshopListResponse,
    WorkshopResponse,
    WorkshopUpdate,
)
from src.services import reservation_service, review_service, workshop_service
from src.services.reservation_queries import get_waitlist_position

router = APIRouter(prefix="/workshops", tags=["workshops"])


@router.post("", response_model=WorkshopResponse, status_code=status.HTTP_201_CREATED)
async def create_workshop(
    payload: WorkshopCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> WorkshopResponse:
    """Create a workshop session as an administrator or organization member.

    When the payload names an ``organization_id``, the caller
    must be a member of that organization (or the configured
    administrator); the check happens in the service layer.
    """
    return await workshop_service.create_workshop(session, payload, user=user)


@router.get("", response_model=WorkshopListResponse)
async def list_workshops(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Search term matched against title and description"),
    ] = None,
    category: Annotated[
        str | None,
        Query(max_length=100, description="Exact category filter"),
    ] = None,
    state: Annotated[
        str,
        Query(pattern="^(upcoming|past|all)$", description="Upcoming, past, or all sessions"),
    ] = "upcoming",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    following: Annotated[
        bool,
        Query(description="Only workshops from organizations the caller follows"),
    ] = False,
) -> WorkshopListResponse:
    """List workshops with search, filter, and pagination.

    The catalogue only ever contains ``published`` sessions for
    the public. The configured administrator sees every
    lifecycle state - draft and cancelled sessions included -
    so the admin management surface can oversee the full
    lifecycle.

    Args:
        q: Optional case-insensitive substring matched against
            the title and description.
        category: Optional exact category filter.
        state: ``upcoming`` (default) hides sessions that have
            started; ``past`` shows only those; ``all`` shows
            both.
        limit: Page size (1..100).
        offset: Number of matching rows to skip.
        following: When ``true``, restrict results to workshops
            from organizations the caller follows (roadmap 2.3).
            Requires a signed-in account - an anonymous request
            with ``following=true`` is a 401 rather than an
            empty page, so the caller learns the filter needs
            authentication instead of silently seeing nothing.
        session: Active async database session.
        user: The signed-in account, when any.

    Returns:
        A ``WorkshopListResponse`` envelope ordered by
        ``starts_at`` ascending.

    Raises:
        HTTPException: 401 when ``following=true`` and the
            caller is anonymous.
    """
    if following and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to filter by followed organizations",
        )
    return await workshop_service.list_workshops(
        session,
        q=q,
        category=category,
        state=state,
        limit=limit,
        offset=offset,
        following_user_id=user.id if following and user is not None else None,
        include_unpublished=user is not None and is_admin(user),
    )


@router.get("/events")
async def stream_events() -> StreamingResponse:
    """Stream reservation events over Server-Sent Events.

    The browser opens a single ``EventSource`` against this URL and
    branches on each event's ``workshop_id`` to update the right
    card on the list view (and the open detail view, if any).

    A heartbeat comment (``: keepalive``) is emitted on the
    interval configured by ``Settings.sse_heartbeat_seconds`` so
    intermediate proxies (nginx, load balancers) do not close the
    connection on idle.

    Returns:
        A ``text/event-stream`` response that streams
        ``reservation_created`` and ``reservation_cancelled`` events.
    """

    async def event_source():
        queue = await subscribe_global()
        try:
            # Initial comment line to flush headers in some proxies.
            yield b": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=get_settings().sse_heartbeat_seconds,
                    )
                    yield format_sse(event)
                except TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            await unsubscribe_global(queue)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/{workshop_id}", response_model=WorkshopDetailResponse)
async def get_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> WorkshopDetailResponse:
    """Fetch a single published workshop and the caller's visible reservations.

    Anonymous requests see an empty reservation list. Signed-in
    requests see only their own active reservations on this
    workshop - this is the privacy boundary enforced by
    ``workshop_service.get_workshop_detail``. The signed-in
    caller's waitlist position is included when they hold a
    place in line.

    The configured administrator may also read draft and
    cancelled sessions, which is what the admin management
    surface needs to edit or re-publish them; every other
    caller gets a 404 for non-published workshops.

    Args:
        workshop_id: Workshop UUID from the URL.
        session: Active async database session.
        user: The signed-in account if any.

    Returns:
        The detail payload with computed ``available_spots``,
        the caller's reservations, and their ``waitlist_position``.

    Raises:
        WorkshopNotFoundError: 404 if no published workshop has
            this id (non-administrators only; the administrator
            may read any state).
    """
    return await workshop_service.get_workshop_detail(
        session,
        workshop_id,
        user.id if user else None,
        include_unpublished=user is not None and is_admin(user),
    )


@router.put("/{workshop_id}", response_model=WorkshopResponse)
async def update_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    payload: WorkshopUpdate,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> WorkshopResponse:
    """Edit a workshop's mutable fields as an administrator or organization member.

    When the payload names an ``organization_id``, ownership is
    reassigned and the caller must be a member of the new
    organization (or the configured administrator).
    """
    return await workshop_service.update_workshop(
        session, workshop_id, payload, user=user
    )


@router.post(
    "/{workshop_id}/publish",
    response_model=WorkshopResponse,
)
async def publish_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> WorkshopResponse:
    """Publish a draft workshop as an administrator or organization member."""
    return await workshop_service.publish_workshop(session, workshop_id)


@router.post(
    "/{workshop_id}/cancel",
    response_model=WorkshopResponse,
)
async def cancel_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> WorkshopResponse:
    """Cancel a workshop as an administrator or organization member.

    Refused with 409 while active reservations exist.
    """
    return await workshop_service.cancel_workshop(session, workshop_id)


@router.post(
    "/{workshop_id}/waitlist",
    response_model=WaitlistJoinResponse,
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Workshop not found or not published"},
        409: {"description": "Registration closed or already booked"},
    },
)
async def join_waitlist(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> WaitlistJoinResponse:
    """Join a workshop's waitlist.

    Requires a signed-in account. Joining is idempotent: a
    caller who already holds an active place in line gets that
    entry back with ``replayed=true``.

    Args:
        workshop_id: Workshop UUID from the URL.
        session: Active async database session.
        user: The signed-in account joining.

    Returns:
        The waitlist entry and the caller's queue position.

    Raises:
        WorkshopNotFoundError: 404 if the workshop is missing
            or not published.
        RegistrationClosedError: 409 if the registration
            window has closed.
        AlreadyReservedError: 409 if the caller already holds
            a seat.
    """
    entry, replayed = await reservation_service.join_waitlist(
        session, workshop_id, user
    )
    position = await get_waitlist_position(session, workshop_id, user.id)
    return WaitlistJoinResponse(
        entry=WaitlistEntryResponse.model_validate(entry),
        position=position if position is not None else 1,
        replayed=replayed,
    )


@router.post(
    "/{workshop_id}/reviews",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Workshop not found or not published"},
        409: {"description": "Session not ended, no past reservation, or already reviewed"},
        422: {"description": "Rating outside the 1..5 range"},
    },
)
async def create_review(
    workshop_id: Annotated[uuid.UUID, Path()],
    payload: ReviewCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ReviewResponse:
    """Review a workshop after attending it (roadmap 2.4).

    Any signed-in account may call this, but the service
    layer accepts the review only when the caller held a
    reservation for the workshop (any status - a cancelled
    booking still counts as having attended) **and** the
    workshop's session has ended (``ends_at``, falling back
    to ``starts_at`` when the workshop declares no explicit
    end). One review per user per workshop: a second review
    is rejected with ``review_already_exists`` before the
    insert, with the unique constraint
    ``uq_reviews_workshop_user`` as the race-free backstop.

    Args:
        workshop_id: Workshop UUID from the URL.
        payload: Validated review body (rating 1..5,
            optional text).
        session: Active async database session.
        user: The signed-in account writing the review.

    Returns:
        The created ``ReviewResponse``.

    Raises:
        WorkshopNotFoundError: 404 if no published workshop
            has this id.
        ReviewNotEligibleError: 409 if the session has not
            ended or the caller never held a reservation.
        ReviewAlreadyExistsError: 409 if the caller already
            reviewed this workshop.
    """
    return await review_service.create_review(
        session, workshop_id, user, payload
    )


@router.delete("/{workshop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_organizer)],
) -> Response:
    """Delete an unbooked workshop session as an administrator or organization member."""
    await workshop_service.delete_workshop(session, workshop_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
