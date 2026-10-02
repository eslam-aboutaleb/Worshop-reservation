"""Custom domain exceptions and their FastAPI handlers.

Every domain-level error condition in the service raises one of the
``*Error`` classes declared here, and ``main.create_app`` registers
the corresponding ``*_handler`` to translate it into a uniform JSON
envelope.

Envelope shape
--------------

Every handler returns

    {
        "error": {
            "code": "<machine_readable>",
            "message": "<human_readable>"
        }
    }

The frontend branches on ``error.code`` rather than parsing the
message, so the human string can be localized later without
breaking clients. Always include a code; never include stack traces
or PII.

Adding a new domain error
-------------------------

1. Declare a subclass of ``Exception`` here. Take any contextual
   data as constructor arguments and store it on ``self``.
2. Implement a ``async def *_handler(request, exc) -> JSONResponse``
   that returns the envelope with the right HTTP status.
3. Register it in :func:`src.main.create_app` with
   ``app.add_exception_handler(MyError, my_error_handler)``.
"""

import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class DomainError(Exception):
    """Base class for errors that have a stable API response."""

    status_code: int
    code: str


class WorkshopNotFoundError(DomainError):
    """Raised when a requested workshop does not exist.

    Carries the offending id on ``self.workshop_id`` so the handler
    can include it in the human message without re-parsing.

    Attributes:
        workshop_id: Stringified UUID of the missing workshop.
    """

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__(f"Workshop {workshop_id} not found")

    status_code = status.HTTP_404_NOT_FOUND
    code = "workshop_not_found"


class ReservationNotFoundError(DomainError):
    """Raised when a requested reservation does not exist or is not visible to the caller.

    Used both for genuine 404s and for the authorization case
    (logged-in user trying to cancel someone else's booking). The
    response is identical so the existence of reservations the
    caller does not own is not leaked.

    Attributes:
        reservation_id: Stringified UUID of the missing reservation.
    """

    def __init__(self, reservation_id: str) -> None:
        self.reservation_id = reservation_id
        super().__init__(f"Reservation {reservation_id} not found")

    status_code = status.HTTP_404_NOT_FOUND
    code = "reservation_not_found"


class WorkshopFullError(DomainError):
    """Raised when a workshop is at its configured maximum capacity.

    ``self.workshop_id`` carries the offending id so the handler
    can include it in the human message.

    Attributes:
        workshop_id: Stringified UUID of the full workshop.
    """

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__(f"Workshop {workshop_id} is at full capacity")

    status_code = status.HTTP_409_CONFLICT
    code = "workshop_full"


class WorkshopHasActiveReservationsError(DomainError):
    """Raised when a booked workshop is requested for deletion."""

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__("Cancel all active reservations before cancelling this workshop")

    status_code = status.HTTP_409_CONFLICT
    code = "workshop_has_active_reservations"


class AlreadyReservedError(DomainError):
    """Raised when an attendee already has an active reservation for the workshop.

    Distinct from ``WorkshopFullError`` so the frontend can show a
    friendlier "you are already booked" message instead of "the
    workshop is full".

    Attributes:
        workshop_id: Stringified UUID of the workshop the caller
            tried (and failed) to reserve again.
    """

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__(f"You already reserved workshop {workshop_id}")

    status_code = status.HTTP_409_CONFLICT
    code = "already_reserved"


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Translate domain errors into the common API error envelope."""
    context: dict[str, str] = {"error_code": exc.code}
    if hasattr(exc, "workshop_id"):
        context["workshop_id"] = exc.workshop_id
    if hasattr(exc, "reservation_id"):
        context["reservation_id"] = exc.reservation_id
    logger.info("domain_error_raised", **context)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )
