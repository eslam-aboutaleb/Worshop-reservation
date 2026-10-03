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
    friendlier "you already have a booking" message instead of "the
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


class RegistrationClosedError(DomainError):
    """Raised when a reservation is attempted outside the registration window.

    Covers both "the session has already started" and "the explicit
    registration deadline has passed". The response is a 409 rather
    than a 404 because the workshop genuinely exists and is visible;
    only the booking window is closed.

    Attributes:
        workshop_id: Stringified UUID of the workshop whose window closed.
        reason: Machine-readable cause (``"started"`` or ``"closed"``)
            so the frontend can pick the right copy.
    """

    def __init__(self, workshop_id: str, reason: str = "closed") -> None:
        self.workshop_id = workshop_id
        self.reason = reason
        super().__init__(
            f"Registration for workshop {workshop_id} is no longer open"
        )

    status_code = status.HTTP_409_CONFLICT
    code = "registration_closed"


class RateLimitedError(DomainError):
    """Raised when a caller exceeds the configured attempt budget.

    Used by the auth rate limiter (plan 0.3). The lockout is
    time-boxed by ``Settings.auth_lockout_seconds``; the frontend
    surfaces the message and disables the form until it clears.

    Attributes:
        retry_after_seconds: How long the caller should wait before
            the next attempt is accepted.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Too many attempts. Try again in {retry_after_seconds} seconds"
        )

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class WaitlistEntryNotFoundError(DomainError):
    """Raised when a waitlist entry does not exist or is not the caller's.

    Mirrors ``ReservationNotFoundError``: a foreign or unknown id
    is a 404 so the API does not leak which waitlist entries exist.

    Attributes:
        entry_id: Stringified UUID of the missing entry.
    """

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"Waitlist entry {entry_id} not found")

    status_code = status.HTTP_404_NOT_FOUND
    code = "waitlist_entry_not_found"


class EmailAlreadyExistsError(DomainError):
    """Raised when a signup attempts to register an existing email.

    The response is a 409 rather than a 401 so the client can
    distinguish "this email is already registered" (try logging
    in) from a bad password.

    Attributes:
        email: The email that is already registered.
    """

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"An account with email {email} already exists")

    status_code = status.HTTP_409_CONFLICT
    code = "email_already_exists"


class OrganizationNotFoundError(DomainError):
    """Raised when a requested organization does not exist or is not visible.

    Used both for genuine 404s and for the authorization case
    (a caller who is not a member of the organization that
    owns a workshop, or that tries to attach a workshop to an
    organization they do not belong to). The response is
    identical in both cases so the existence of organizations
    the caller does not belong to is not leaked - the same
    no-leak philosophy as ``ReservationNotFoundError``.

    Attributes:
        organization_id: Stringified UUID of the missing
            organization.
    """

    def __init__(self, organization_id: str) -> None:
        self.organization_id = organization_id
        super().__init__(f"Organization {organization_id} not found")

    status_code = status.HTTP_404_NOT_FOUND
    code = "organization_not_found"


class InvalidCredentialsError(DomainError):
    """Raised when login credentials are incorrect.

    The same response is returned for an unknown email and a
    wrong password so the endpoint does not disclose which
    emails are registered.
    """

    def __init__(self) -> None:
        super().__init__("Invalid email or password")

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"


class CannotFollowOwnOrganizationError(DomainError):
    """Raised when a caller tries to follow an organization they belong to.

    A member (owner or plain member) already has a stronger
    relationship with the organization than a follow, so the
    follow endpoint rejects the request rather than creating
    a redundant row. The response is a 409 rather than a 404
    because the organization genuinely exists and is visible
    to the caller; only the requested relationship is invalid.

    Attributes:
        organization_id: Stringified UUID of the organization
            the caller tried to follow.
    """

    def __init__(self, organization_id: str) -> None:
        self.organization_id = organization_id
        super().__init__(
            f"Cannot follow organization {organization_id}: "
            "you are already a member"
        )

    status_code = status.HTTP_409_CONFLICT
    code = "cannot_follow_own_organization"


class ReviewNotEligibleError(DomainError):
    """Raised when a caller may not review a workshop.

    A review requires both a held reservation (any status -
    a cancelled booking still counts as having attended) and
    a session that has ended (``ends_at``, falling back to
    ``starts_at``). The 409 (rather than 403 or 404) keeps
    the no-leak philosophy: the workshop exists and is
    visible, but the caller's relationship to it does not
    permit a review yet.

    Attributes:
        workshop_id: Stringified UUID of the workshop the
            caller tried to review.
    """

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__(
            f"Workshop {workshop_id} can only be reviewed by "
            "past attendees after the session has ended"
        )

    status_code = status.HTTP_409_CONFLICT
    code = "review_not_eligible"


class ReviewAlreadyExistsError(DomainError):
    """Raised when a caller reviews a workshop they already reviewed.

    One review per user per workshop is the invariant. The
    application checks for an existing row first so the caller
    gets this friendly 409; the unique constraint
    ``uq_reviews_workshop_user`` is the race-free backstop.

    Attributes:
        workshop_id: Stringified UUID of the workshop the
            caller tried to review twice.
    """

    def __init__(self, workshop_id: str) -> None:
        self.workshop_id = workshop_id
        super().__init__(f"You already reviewed workshop {workshop_id}")

    status_code = status.HTTP_409_CONFLICT
    code = "review_already_exists"


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Translate domain errors into the common API error envelope."""
    context: dict[str, str] = {"error_code": exc.code}
    if hasattr(exc, "workshop_id"):
        context["workshop_id"] = exc.workshop_id
    if hasattr(exc, "reservation_id"):
        context["reservation_id"] = exc.reservation_id
    if hasattr(exc, "organization_id"):
        context["organization_id"] = exc.organization_id
    logger.info("domain_error_raised", **context)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )
