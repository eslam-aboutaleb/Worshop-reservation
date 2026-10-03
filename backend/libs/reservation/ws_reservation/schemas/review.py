"""Pydantic schemas for the reviews surface (roadmap 2.4).

Reviews are written by attendees **after** a workshop's
session has ended and only when they held a reservation
(enforced in ``services/review_service.py``). The create
payload validates the rating bounds at the schema layer so
an out-of-range rating is a 422 before any database work
happens.

Two response shapes exist:

* ``ReviewResponse`` - the full row, returned by the
  write endpoint to the reviewer (it includes the
  reviewer's own ``user_id``).
* ``WorkshopReviewResponse`` - the public view embedded in
  the workshop detail payload. It carries the reviewer's
  display name instead of their id, following the
  ``MyWaitlistEntryResponse`` pattern of a subclass
  enriched with a joined field.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ReviewCreate(BaseModel):
    """Validated payload for ``POST /api/workshops/{id}/reviews``.

    Attributes:
        rating: Integer rating in 1..5. Bounds are enforced
            here (422) and by the ``ck_reviews_rating``
            database CHECK as a backstop.
        text: Optional free-form comment (max 4000 chars).
    """

    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=4000)

    @field_validator("text")
    @classmethod
    def text_strip_whitespace(cls, value: str) -> str:
        """Trim the comment without rejecting an empty one."""
        return value.strip()


class ReviewResponse(BaseModel):
    """A review as returned to its author by the write endpoint.

    Attributes:
        id: Review UUID.
        workshop_id: Parent workshop UUID.
        user_id: Reviewing account UUID.
        rating: Integer rating in 1..5.
        text: The comment (possibly empty).
        created_at: Review creation timestamp.
    """

    id: uuid.UUID
    workshop_id: uuid.UUID
    user_id: uuid.UUID
    rating: int
    text: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkshopReviewResponse(ReviewResponse):
    """Review enriched with the reviewer's display name.

    Used by the workshop detail payload so the public
    review list can show who wrote each review without a
    second round-trip per row. The reviewer's ``user_id``
    is retained for API consistency; the display name is
    joined in ``services/review_service.list_workshop_reviews``.

    Attributes:
        user_name: Display name of the reviewing account.
    """

    user_name: str
