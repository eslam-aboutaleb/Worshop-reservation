"""Pydantic schemas for the waitlist surface.

The waitlist is a place in line, not a seat. These schemas
mirror the ``WaitlistEntry`` model for the API responses;
the join response (entry + position) lives in
``schemas/workshop.WaitlistJoinResponse`` because it is
returned by the workshop-scoped join endpoint.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class WaitlistEntryResponse(BaseModel):
    """A place in line on a workshop's waitlist.

    Attributes:
        id: Waitlist entry UUID.
        workshop_id: Parent workshop UUID.
        status: ``active`` / ``promoted`` / ``cancelled``.
        created_at: When the caller joined the waitlist.
        promoted_at: When the entry was promoted, or ``None``.
    """

    id: uuid.UUID
    workshop_id: uuid.UUID
    status: str
    created_at: datetime
    promoted_at: datetime | None = None

    model_config = {"from_attributes": True}


class MyWaitlistEntryResponse(WaitlistEntryResponse):
    """Waitlist entry enriched with its workshop title and queue position.

    Used by ``GET /api/waitlist/me`` so the signed-in
    account view can show a human-friendly label and the
    caller's place in line for each active entry without a
    second round-trip per row.

    Attributes:
        workshop_title: Title of the parent workshop, joined
            in ``reservation_queries.list_user_waitlist_entries``.
        position: The caller's 1-based position in the
            workshop's queue.
    """

    workshop_title: str
    position: int
