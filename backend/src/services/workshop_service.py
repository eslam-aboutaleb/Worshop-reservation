"""Read-side service functions for the workshop surface.

This module is responsible for the **list**, **detail** and
**lifecycle** queries exposed under ``/api/workshops``.
Write-side reservation mutations live in
``services/reservation_service`` so the row-locked capacity
check is in one place; the workshop lifecycle transitions
(create / edit / publish / cancel / delete) live here
because they mutate the ``workshops`` table itself.

``available_spots`` is a *computed* field, not a column. It
is computed on every read instead of maintaining a counter so
the read path is trivially consistent with the reservations
table. The cost is a ``COUNT(*)`` (or correlated subquery) per
row, which is negligible at the project's expected scale.

Discovery (plan 1.3)
---------------------

``list_workshops`` supports full-text search (``q`` matches
title or description via ``ILIKE``), a ``category`` filter, a
``state`` filter (``upcoming`` / ``past`` / ``all``), and
offset pagination. The response is a paginated envelope
``{items, total, limit, offset}``.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth import is_admin
from ws_core.errors import (
    OrganizationNotFoundError,
    WorkshopHasActiveReservationsError,
    WorkshopNotFoundError,
)
from ws_core.realtime import publish

from src.models.organization import OrganizationFollow, OrganizationMembership
from src.models.reservation import RESERVATION_STATUS_ACTIVE, Reservation
from src.models.workshop import (
    WORKSHOP_STATUS_CANCELLED,
    WORKSHOP_STATUS_PUBLISHED,
    Workshop,
)
from src.schemas.workshop import (
    ReservationSummary,
    WorkshopCreate,
    WorkshopDetailResponse,
    WorkshopListResponse,
    WorkshopResponse,
    WorkshopUpdate,
)
from src.services.reservation_queries import (
    count_active_reservations,
    get_waitlist_position,
)
from src.services.reservation_service import cancel_waitlist_for_workshop
from src.services.review_service import (
    get_review_aggregate,
    list_workshop_reviews,
)

logger = structlog.get_logger(__name__)

# Default page size for the discovery list. Small on purpose:
# the catalogue is dozens of sessions, not millions.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _workshop_to_response(workshop: Workshop, available_spots: int) -> WorkshopResponse:
    """Build a ``WorkshopResponse`` from an ORM row and a spot count."""
    return WorkshopResponse(
        id=workshop.id,
        title=workshop.title,
        starts_at=workshop.starts_at,
        ends_at=workshop.ends_at,
        max_capacity=workshop.max_capacity,
        available_spots=max(available_spots, 0),
        registration_closes_at=workshop.registration_closes_at,
        status=workshop.status,
        organization_id=workshop.organization_id,
        created_at=workshop.created_at,
    )


async def _require_organization_member(
    session: AsyncSession,
    user,
    organization_id: uuid.UUID,
) -> None:
    """Refuse to attach a workshop to an organization the caller does not belong to.

    Used by the create and edit paths when the payload names an
    owning organization. The configured super-admin bypasses the
    membership check; every other caller must hold an
    ``owner``/``member`` membership in the named organization.

    Args:
        session: Active async database session.
        user: The calling account, or ``None`` for privileged
            service-level calls that do not name an organization.
        organization_id: The organization being attached.

    Raises:
        OrganizationNotFoundError: If ``user`` is ``None``, is not
            the configured administrator, or holds no membership in
            ``organization_id``. The 404 (rather than 403) keeps the
            no-leak philosophy: a caller cannot probe which
            organizations exist.
    """
    if user is not None and is_admin(user):
        return
    if user is None:
        raise OrganizationNotFoundError(str(organization_id))
    member = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise OrganizationNotFoundError(str(organization_id))


async def list_workshops(
    session: AsyncSession,
    *,
    q: str | None = None,
    category: str | None = None,
    state: str = "upcoming",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    following_user_id: uuid.UUID | None = None,
    include_unpublished: bool = False,
) -> WorkshopListResponse:
    """Return a paginated, filtered view of the catalogue.

    The active-reservation count is computed via a correlated
    subquery so the list endpoint stays to a single round trip
    even for a large workshop catalogue. Only ``published``
    workshops are listed to the public; draft and cancelled
    sessions are hidden from the catalogue. The configured
    administrator may pass ``include_unpublished`` to see
    every state, keeping the full lifecycle manageable.

    When ``following_user_id`` is set (the discovery list's
    ``following=true`` filter, roadmap 2.3), only workshops
    owned by organizations that account follows are returned.
    Platform-managed workshops (``organization_id IS NULL``)
    are never matched by the filter - they belong to no
    organization to follow.

    Args:
        session: Active async database session.
        q: Optional search term matched against title and
            description (case-insensitive substring).
        category: Optional exact category filter.
        state: ``upcoming`` (default), ``past``, or ``all``.
            ``upcoming`` filters to sessions that have not
            started; ``past`` to sessions that have.
        limit: Page size, clamped to ``[1, MAX_PAGE_SIZE]``.
        offset: Number of matching rows to skip.
        following_user_id: Optional account id; when set,
            restricts results to workshops from organizations
            that account follows.
        include_unpublished: When ``True``, draft and
            cancelled sessions are listed alongside published
            ones. Reserved for the configured administrator
            (the admin management surface).

    Returns:
        A ``WorkshopListResponse`` envelope ordered by
        ``starts_at`` ascending (soonest first), with ties on
        ``starts_at`` falling back to ``created_at`` for a
        stable order across refreshes.
    """
    clamped_limit = max(1, min(limit, MAX_PAGE_SIZE))
    safe_offset = max(0, offset)
    now = datetime.now(UTC)

    # The public catalogue only ever shows published sessions;
    # the administrator's management view lists every state
    # so cancelled and draft sessions stay reachable.
    conditions = (
        [] if include_unpublished else [Workshop.status == WORKSHOP_STATUS_PUBLISHED]
    )
    if state == "upcoming":
        conditions.append(Workshop.starts_at >= now)
    elif state == "past":
        conditions.append(Workshop.starts_at < now)
    if q:
        pattern = f"%{q}%"
        conditions.append(or_(Workshop.title.ilike(pattern), Workshop.description.ilike(pattern)))
    if category:
        conditions.append(Workshop.category == category)
    if following_user_id is not None:
        followed_organizations = select(OrganizationFollow.organization_id).where(
            OrganizationFollow.user_id == following_user_id
        )
        conditions.append(Workshop.organization_id.in_(followed_organizations))

    active_count = (
        select(func.count(Reservation.id))
        .where(Reservation.workshop_id == Workshop.id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
        .correlate(Workshop)
        .scalar_subquery()
    )

    total = (
        await session.execute(select(func.count(Workshop.id)).where(*conditions))
    ).scalar_one()

    stmt = (
        select(Workshop, (Workshop.max_capacity - active_count).label("available_spots"))
        .where(*conditions)
        .order_by(Workshop.starts_at.asc(), Workshop.created_at.asc())
        .limit(clamped_limit)
        .offset(safe_offset)
    )
    result = await session.execute(stmt)
    rows = result.all()
    items = [
        _workshop_to_response(workshop, available_spots)
        for workshop, available_spots in rows
    ]
    return WorkshopListResponse(
        items=items,
        total=int(total),
        limit=clamped_limit,
        offset=safe_offset,
    )


async def create_workshop(
    session: AsyncSession,
    payload: WorkshopCreate,
    user=None,
) -> WorkshopResponse:
    """Persist and broadcast a new bookable workshop.

    When ``payload.organization_id`` is set, the workshop is
    attached to that organization and the caller must be the
    configured administrator or a member of it (see
    ``_require_organization_member``); otherwise the workshop
    is created as a platform-managed session.

    Args:
        session: Active async database session.
        payload: Validated workshop payload.
        user: The calling account, resolved by the
            ``get_current_organizer`` dependency. Direct
            service-level calls may omit it, but only when
            the payload names no organization.

    Returns:
        The created ``WorkshopResponse``.

    Raises:
        OrganizationNotFoundError: If the payload names an
            organization the caller does not belong to.
    """
    if payload.organization_id is not None:
        await _require_organization_member(session, user, payload.organization_id)
    workshop = Workshop(
        title=payload.title.strip(),
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        max_capacity=payload.max_capacity,
        description=payload.description,
        category=payload.category,
        location=payload.location,
        registration_closes_at=payload.registration_closes_at,
        organization_id=payload.organization_id,
        status=WORKSHOP_STATUS_PUBLISHED,
    )
    session.add(workshop)
    await session.commit()
    await session.refresh(workshop)
    response = _workshop_to_response(workshop, available_spots=workshop.max_capacity)
    await publish(
        workshop.id,
        {"workshop_id": str(workshop.id), "type": "workshop_created"},
    )
    logger.info("workshop.created", workshop_id=str(workshop.id))
    return response


async def update_workshop(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    payload: WorkshopUpdate,
    user=None,
) -> WorkshopResponse:
    """Edit a workshop's mutable fields (plan 1.1).

    Only the fields present in ``payload`` are changed; the
    rest keep their current value. Lifecycle transitions are
    deliberately **not** part of the edit payload - use the
    dedicated publish/cancel endpoints so a free-form edit
    cannot accidentally un-publish a live session.

    When ``payload.organization_id`` is set, ownership is
    reassigned to that organization and the caller must be
    the configured administrator or a member of it (see
    ``_require_organization_member``). Omitting the field
    keeps the current ownership; clearing it is not
    supported through this payload.

    Args:
        session: Active async database session.
        workshop_id: Workshop to edit.
        payload: Fields to change; ``None`` fields are ignored.
        user: The calling account, resolved by the
            ``get_current_organizer`` dependency. Direct
            service-level calls may omit it, but only when
            the payload names no organization.

    Returns:
        The updated ``WorkshopResponse``.

    Raises:
        WorkshopNotFoundError: If no workshop has this id.
        OrganizationNotFoundError: If the payload reassigns
            the workshop to an organization the caller does
            not belong to.
    """
    if payload.organization_id is not None:
        await _require_organization_member(session, user, payload.organization_id)
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    if payload.title is not None:
        workshop.title = payload.title
    if payload.starts_at is not None:
        workshop.starts_at = payload.starts_at
    if payload.ends_at is not None:
        workshop.ends_at = payload.ends_at
    if payload.max_capacity is not None:
        workshop.max_capacity = payload.max_capacity
    if payload.description is not None:
        workshop.description = payload.description
    if payload.category is not None:
        workshop.category = payload.category
    if payload.location is not None:
        workshop.location = payload.location
    if payload.registration_closes_at is not None:
        workshop.registration_closes_at = payload.registration_closes_at
    if payload.organization_id is not None:
        workshop.organization_id = payload.organization_id

    await session.commit()
    await session.refresh(workshop)
    available_spots = max(
        workshop.max_capacity - await count_active_reservations(session, workshop_id),
        0,
    )
    await publish(
        workshop.id,
        {"workshop_id": str(workshop.id), "type": "workshop_updated"},
    )
    logger.info("workshop.updated", workshop_id=str(workshop_id))
    return _workshop_to_response(workshop, available_spots)


async def publish_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> WorkshopResponse:
    """Transition a draft workshop to ``published`` (plan 1.1).

    Args:
        session: Active async database session.
        workshop_id: Workshop to publish.

    Returns:
        The published ``WorkshopResponse``.

    Raises:
        WorkshopNotFoundError: If no workshop has this id.
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    workshop.status = WORKSHOP_STATUS_PUBLISHED
    await session.commit()
    await session.refresh(workshop)
    available_spots = max(
        workshop.max_capacity - await count_active_reservations(session, workshop_id),
        0,
    )
    await publish(
        workshop.id,
        {"workshop_id": str(workshop.id), "type": "workshop_published"},
    )
    logger.info("workshop.published", workshop_id=str(workshop_id))
    return _workshop_to_response(workshop, available_spots)


async def cancel_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> WorkshopResponse:
    """Transition a workshop to ``cancelled`` (plan 1.1).

    Refuses when active reservations exist (reusing
    ``WorkshopHasActiveReservationsError``) so attendees are
    never silently dropped. Cancelling also cancels the
    workshop's active waitlist entries and publishes a
    ``waitlist_cancelled`` event.

    Args:
        session: Active async database session.
        workshop_id: Workshop to cancel.

    Returns:
        The cancelled ``WorkshopResponse``.

    Raises:
        WorkshopNotFoundError: If no workshop has this id.
        WorkshopHasActiveReservationsError: If attendees are
            still booked.
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    active_count = await count_active_reservations(session, workshop_id)
    if active_count:
        raise WorkshopHasActiveReservationsError(str(workshop_id))

    workshop.status = WORKSHOP_STATUS_CANCELLED
    await cancel_waitlist_for_workshop(session, workshop_id)
    await session.commit()
    await session.refresh(workshop)
    available_spots = max(
        workshop.max_capacity - await count_active_reservations(session, workshop_id),
        0,
    )
    await publish(
        workshop.id,
        {"workshop_id": str(workshop.id), "type": "workshop_cancelled"},
    )
    logger.info("workshop.cancelled", workshop_id=str(workshop_id))
    return _workshop_to_response(workshop, available_spots)


async def delete_workshop(session: AsyncSession, workshop_id: uuid.UUID) -> None:
    """Delete an unbooked workshop and notify connected catalogues.

    Deleting also cancels the workshop's active waitlist
    entries (plan 1.2) and publishes ``waitlist_cancelled`` so
    waitlisted users see their place in line disappear.

    Args:
        session: Active async database session.
        workshop_id: Workshop to delete.

    Raises:
        WorkshopNotFoundError: If no workshop has this id.
        WorkshopHasActiveReservationsError: If attendees are
            still booked.
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id).with_for_update())
    ).scalar_one_or_none()
    if workshop is None:
        raise WorkshopNotFoundError(str(workshop_id))

    active_count = await session.scalar(
        select(func.count(Reservation.id))
        .where(Reservation.workshop_id == workshop_id)
        .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
    )
    if active_count:
        raise WorkshopHasActiveReservationsError(str(workshop_id))

    await cancel_waitlist_for_workshop(session, workshop_id)
    await session.execute(delete(Workshop).where(Workshop.id == workshop_id))
    await session.commit()
    await publish(workshop_id, {"workshop_id": str(workshop_id), "type": "workshop_deleted"})
    logger.info("workshop.deleted", workshop_id=str(workshop_id))


async def get_workshop_detail(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    include_unpublished: bool = False,
) -> WorkshopDetailResponse:
    """Return a single published workshop with its active reservations.

    Privacy model: the embedded ``reservations`` list is filtered
    to active rows owned by ``user_id`` when a signed-in caller
    is identified; anonymous calls always see an empty list. This
    matches the schema's documentation and the brief's privacy
    expectations (a workshop page shows "you are booked", not
    "everyone is booked").

    The detail payload also carries the review aggregate
    (average rating and count) and the full review list,
    newest first (roadmap 2.4). Reviews are public: anyone
    may read them, only past attendees may write them.

    Non-published workshops are 404-shaped so draft and cancelled
    sessions are not discoverable through the detail endpoint.
    The configured administrator may pass ``include_unpublished``
    to read any state, which is what the admin management
    surface needs to edit or re-publish a cancelled session.

    Args:
        session: Active async database session.
        workshop_id: Workshop to fetch.
        user_id: Optional account UUID; when provided, only that
            account's own active reservations are returned, and the
            account's waitlist position is computed.
        include_unpublished: When ``True``, non-published
            workshops are returned instead of 404-shaped.
            Reserved for the configured administrator.

    Returns:
        A ``WorkshopDetailResponse`` with the computed
        ``available_spots``, the caller's visible reservations,
        the caller's ``waitlist_position`` (or ``None``), the
        rating aggregate, and the review list.

    Raises:
        WorkshopNotFoundError: If no published workshop exists
            with ``workshop_id`` (or the caller is not the
            administrator and the workshop is not published).
    """
    workshop = (
        await session.execute(select(Workshop).where(Workshop.id == workshop_id))
    ).scalar_one_or_none()
    if workshop is None or (
        workshop.status != WORKSHOP_STATUS_PUBLISHED and not include_unpublished
    ):
        raise WorkshopNotFoundError(str(workshop_id))

    active_count = await count_active_reservations(session, workshop_id)
    available_spots = max(workshop.max_capacity - active_count, 0)

    if user_id is None:
        visible_reservations: list[Reservation] = []
        waitlist_position: int | None = None
    else:
        visible_reservations = list(
            (
                await session.execute(
                    select(Reservation)
                    .where(Reservation.workshop_id == workshop_id)
                    .where(Reservation.user_id == user_id)
                    .where(Reservation.status == RESERVATION_STATUS_ACTIVE)
                )
            )
            .scalars()
            .all()
        )
        waitlist_position = await get_waitlist_position(session, workshop_id, user_id)

    rating_average, rating_count = await get_review_aggregate(
        session, workshop_id
    )
    reviews = await list_workshop_reviews(session, workshop_id)

    return WorkshopDetailResponse(
        id=workshop.id,
        title=workshop.title,
        starts_at=workshop.starts_at,
        ends_at=workshop.ends_at,
        max_capacity=workshop.max_capacity,
        available_spots=available_spots,
        description=workshop.description,
        category=workshop.category,
        location=workshop.location,
        registration_closes_at=workshop.registration_closes_at,
        status=workshop.status,
        organization_id=workshop.organization_id,
        reservations=[
            ReservationSummary(
                id=reservation.id,
                attendee_name=reservation.attendee_name,
                attendee_email=reservation.attendee_email,
                created_at=reservation.created_at,
            )
            for reservation in visible_reservations
        ],
        waitlist_position=waitlist_position,
        rating_average=rating_average,
        rating_count=rating_count,
        reviews=reviews,
        created_at=workshop.created_at,
    )
