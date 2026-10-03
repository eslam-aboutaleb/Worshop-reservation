"""Write-side and read-side service functions for reviews.

Reviews (roadmap 2.4) are gated on two conditions,
both enforced here rather than at the router:

1. the caller **held** a reservation for the
   workshop - any status counts, because a cancelled
   booking still means the person was signed up for
   the session, and
2. the workshop's session has **ended** -
   ``ends_at`` when the workshop declares one,
   ``starts_at`` otherwise.

The eligibility check is a query against
``reservations`` joined with the workshop row, so a
caller cannot review a session that has not happened
yet or one they never signed up for. One review per
user per workshop is the invariant: the application
checks for an existing row first (friendly 4xx with
a stable code) and the unique constraint
``uq_reviews_workshop_user`` is the race-free
backstop.

Read side: the workshop detail payload embeds the
rating aggregate (average + count) and the review
list, newest first, each row enriched with the
reviewer's display name.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ws_core.auth.models import User
from ws_core.errors import (
    ReviewAlreadyExistsError,
    ReviewNotEligibleError,
    WorkshopNotFoundError,
)

from src.models.reservation import Reservation
from src.models.review import Review
from src.models.workshop import WORKSHOP_STATUS_PUBLISHED, Workshop
from src.schemas.review import ReviewCreate, ReviewResponse, WorkshopReviewResponse

logger = structlog.get_logger(__name__)


async def find_review(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Review | None:
    """Return the caller's review of a workshop, if any.

    Args:
        session: Active async database session.
        workshop_id: Workshop that may have been reviewed.
        user_id: Account that may have reviewed it.

    Returns:
        The ``Review`` row, or ``None`` when the user
        has not reviewed the workshop.
    """
    statement = select(Review).where(
        Review.workshop_id == workshop_id,
        Review.user_id == user_id,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _held_reservation(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Reservation | None:
    """Return any reservation the user held for the workshop.

    Any status qualifies - active or cancelled - because
    both mean the account was signed up for the session.

    Args:
        session: Active async database session.
        workshop_id: Workshop the user may have booked.
        user_id: Account to look up.

    Returns:
        A matching ``Reservation`` row, or ``None``.
    """
    statement = (
        select(Reservation)
        .where(Reservation.workshop_id == workshop_id)
        .where(Reservation.user_id == user_id)
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def create_review(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    user,
    payload: ReviewCreate,
) -> ReviewResponse:
    """Create a review for a workshop as ``user``.

    The workshop must be published (non-published
    workshops are 404-shaped, matching the detail
    endpoint's no-leak philosophy), the session must
    have ended, and the caller must have held a
    reservation. A second review for the same
    workshop is rejected before the insert.

    Args:
        session: Active async database session.
        workshop_id: Workshop being reviewed.
        user: The signed-in account writing the review.
        payload: Validated review payload (rating 1..5,
            optional text).

    Returns:
        The created ``ReviewResponse``.

    Raises:
        WorkshopNotFoundError: 404 if no published
            workshop has this id.
        ReviewNotEligibleError: 409 if the session has
            not ended or the caller never held a
            reservation.
        ReviewAlreadyExistsError: 409 if the caller
            already reviewed this workshop.
    """
    workshop = (
        await session.execute(
            select(Workshop).where(Workshop.id == workshop_id)
        )
    ).scalar_one_or_none()
    if workshop is None or workshop.status != WORKSHOP_STATUS_PUBLISHED:
        raise WorkshopNotFoundError(str(workshop_id))

    ended_at = workshop.ends_at or workshop.starts_at
    if ended_at >= datetime.now(UTC):
        raise ReviewNotEligibleError(str(workshop_id))

    held = await _held_reservation(session, workshop_id, user.id)
    if held is None:
        raise ReviewNotEligibleError(str(workshop_id))

    existing = await find_review(session, workshop_id, user.id)
    if existing is not None:
        raise ReviewAlreadyExistsError(str(workshop_id))

    review = Review(
        workshop_id=workshop_id,
        user_id=user.id,
        rating=payload.rating,
        text=payload.text,
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)
    logger.info(
        "review.created",
        workshop_id=str(workshop_id),
        user_id=str(user.id),
        rating=payload.rating,
    )
    return ReviewResponse.model_validate(review)


async def get_review_aggregate(
    session: AsyncSession,
    workshop_id: uuid.UUID,
) -> tuple[float | None, int]:
    """Return the workshop's average rating and review count.

    Args:
        session: Active async database session.
        workshop_id: Workshop to aggregate.

    Returns:
        A ``(average, count)`` pair. ``average`` is
        ``None`` when the workshop has no reviews;
        otherwise it is rounded to two decimals.
    """
    statement = (
        select(func.avg(Review.rating), func.count(Review.id))
        .where(Review.workshop_id == workshop_id)
    )
    average, count = (await session.execute(statement)).one()
    return (round(float(average), 2) if average is not None else None, int(count))


async def list_workshop_reviews(
    session: AsyncSession,
    workshop_id: uuid.UUID,
) -> list[WorkshopReviewResponse]:
    """Return a workshop's reviews, newest first.

    Each row is joined with the reviewer's display
    name so the detail view can render "who said
    what" without a second round-trip per row.

    Args:
        session: Active async database session.
        workshop_id: Workshop whose reviews to list.

    Returns:
        ``WorkshopReviewResponse`` rows ordered by
        ``created_at`` descending.
    """
    statement = (
        select(Review, User.full_name)
        .join(User, User.id == Review.user_id)
        .where(Review.workshop_id == workshop_id)
        .order_by(Review.created_at.desc())
    )
    rows = (await session.execute(statement)).all()
    return [
        WorkshopReviewResponse(
            **ReviewResponse.model_validate(review).model_dump(),
            user_name=user_name,
        )
        for review, user_name in rows
    ]
