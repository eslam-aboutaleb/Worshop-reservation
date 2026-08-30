"""Workshop browsing and live availability endpoints.

Three endpoints, all under the ``/workshops`` prefix:

* ``GET /``              - list every workshop with current spot counts.
* ``GET /events``        - single SSE stream of create/cancel events.
* ``GET /{workshop_id}`` - detail (active reservations visible to the
  caller only).

SSE stream
----------

The single ``/events`` endpoint is the only SSE stream exposed. It
emits one event per reservation mutation, with a ``workshop_id``
field the client can filter on. We use one channel rather than
per-workshop channels because (a) the filter is trivial on the
client and (b) it lets a single connection power both the list view
and any open detail view.
"""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import get_current_admin, get_optional_user
from src.configuration.database import get_db
from src.configuration.settings import get_settings
from src.models.user import User
from src.realtime import format_sse, subscribe_global, unsubscribe_global
from src.schemas.workshop import WorkshopCreate, WorkshopDetailResponse, WorkshopResponse
from src.services import workshop_service

router = APIRouter(prefix="/workshops", tags=["workshops"])


@router.post("", response_model=WorkshopResponse, status_code=status.HTTP_201_CREATED)
async def create_workshop(
    payload: WorkshopCreate,
    session: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(get_current_admin)],
) -> WorkshopResponse:
    """Create a workshop session as the configured administrator."""
    return await workshop_service.create_workshop(session, payload)


@router.get("", response_model=list[WorkshopResponse])
async def list_workshops(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkshopResponse]:
    """List all workshops with their current available spot counts.

    Args:
        session: Active async database session.

    Returns:
        A list of workshop summaries. The list is not paginated -
        the catalog is expected to be small (dozens, not millions).
    """
    return await workshop_service.list_workshops(session)


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
    """Fetch a single workshop and the caller's visible reservations.

    Anonymous requests see an empty reservation list. Signed-in
    requests see only their own active reservations on this
    workshop - this is the privacy boundary enforced by
    ``workshop_service.get_workshop_detail``.

    Args:
        workshop_id: Workshop UUID from the URL.
        session: Active async database session.
        user: The signed-in account if any.

    Returns:
        The detail payload with computed ``available_spots`` and
        the caller's reservations.

    Raises:
        WorkshopNotFoundError: 404 if no workshop has this id.
    """
    return await workshop_service.get_workshop_detail(
        session, workshop_id, user.id if user else None
    )


@router.delete("/{workshop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workshop(
    workshop_id: Annotated[uuid.UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(get_current_admin)],
) -> Response:
    """Cancel an unbooked workshop session as the configured administrator."""
    await workshop_service.delete_workshop(session, workshop_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
