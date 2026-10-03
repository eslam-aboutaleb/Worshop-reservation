"""Application domain events and the app-level event bus.

Services publish domain events here instead of calling
the realtime bus directly. The events carry the exact
SSE payloads the frontend consumes today - the wire
contract is unchanged; only the indirection is new.

The bus singleton mirrors the realtime default-bus
pattern: :func:`get_event_bus` lazily creates an
in-process bus, :func:`set_event_bus` replaces it
(tests inject a mock to assert on emitted events).
The realtime projector (``src/realtime_projector.py``)
subscribes once and translates events onto the
realtime bus.

Event payloads (byte-identical to the pre-event-bus
SSE broadcasts)
--------------------------------------------------------

* ``ReservationCreated`` - ``{workshop_id, type,
  available_spots, reservation}`` where ``reservation``
  is the public ``{"status": ...}`` view.
* ``ReservationCancelled`` / ``WaitlistPromoted`` -
  ``{workshop_id, type, available_spots}``.
* ``WaitlistJoined`` / ``WaitlistLeft`` /
  ``WaitlistCancelled`` - ``{workshop_id, type}``.
* ``WorkshopCreated`` / ``WorkshopUpdated`` /
  ``WorkshopPublished`` / ``WorkshopCancelled`` /
  ``WorkshopDeleted`` - ``{workshop_id, type}``.

These classes move into the ``ws-reservation`` plugin
in plan 03; they live here until then.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from ws_core.events import DomainEvent, EventBus
from ws_core.events.in_process import InProcessEventBus

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the app-level event bus, creating it on first use.

    Returns:
        The process-wide :class:`~ws_core.events.EventBus`.
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = InProcessEventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """Replace the app-level event bus.

    Used by tests to inject a mock and assert on the
    events a flow emitted.

    Args:
        bus: The bus that :func:`get_event_bus` returns.
    """
    global _event_bus
    _event_bus = bus


@dataclass(kw_only=True)
class ReservationCreated(DomainEvent):
    """A seat was reserved on a published workshop.

    Attributes:
        workshop_id: The workshop that was booked.
        available_spots: Spots left after the booking.
        reservation: Public reservation view (``status`` only -
            the broadcast channel is unauthenticated, so identity
            and booking codes are deliberately excluded).
    """

    workshop_id: uuid.UUID
    available_spots: int
    reservation: dict[str, Any]

    def __post_init__(self) -> None:
        self.type = "reservation_created"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
            "available_spots": self.available_spots,
            "reservation": self.reservation,
        }


@dataclass(kw_only=True)
class ReservationCancelled(DomainEvent):
    """A reservation was cancelled.

    Attributes:
        workshop_id: The workshop whose booking was cancelled.
        available_spots: Spots left after the cancellation.
    """

    workshop_id: uuid.UUID
    available_spots: int

    def __post_init__(self) -> None:
        self.type = "reservation_cancelled"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
            "available_spots": self.available_spots,
        }


@dataclass(kw_only=True)
class WaitlistPromoted(DomainEvent):
    """A waitlist entry was promoted to a reservation.

    Attributes:
        workshop_id: The workshop the entry belonged to.
        available_spots: Spots left after the promotion.
    """

    workshop_id: uuid.UUID
    available_spots: int

    def __post_init__(self) -> None:
        self.type = "waitlist_promoted"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
            "available_spots": self.available_spots,
        }


@dataclass(kw_only=True)
class WaitlistJoined(DomainEvent):
    """An attendee joined a workshop's waitlist.

    Attributes:
        workshop_id: The workshop whose waitlist grew.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "waitlist_joined"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WaitlistLeft(DomainEvent):
    """An attendee left a workshop's waitlist.

    Attributes:
        workshop_id: The workshop whose waitlist shrank.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "waitlist_left"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WaitlistCancelled(DomainEvent):
    """A workshop's waitlist entries were bulk-cancelled.

    Attributes:
        workshop_id: The workshop whose waitlist was cleared.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "waitlist_cancelled"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WorkshopCreated(DomainEvent):
    """A workshop was created.

    Attributes:
        workshop_id: The new workshop.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "workshop_created"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WorkshopUpdated(DomainEvent):
    """A workshop's details were updated.

    Attributes:
        workshop_id: The updated workshop.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "workshop_updated"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WorkshopPublished(DomainEvent):
    """A draft workshop was published.

    Attributes:
        workshop_id: The published workshop.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "workshop_published"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WorkshopCancelled(DomainEvent):
    """A published workshop was cancelled.

    Attributes:
        workshop_id: The cancelled workshop.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "workshop_cancelled"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }


@dataclass(kw_only=True)
class WorkshopDeleted(DomainEvent):
    """A workshop was deleted.

    Attributes:
        workshop_id: The deleted workshop.
    """

    workshop_id: uuid.UUID

    def __post_init__(self) -> None:
        self.type = "workshop_deleted"
        self.payload = {
            "workshop_id": str(self.workshop_id),
            "type": self.type,
        }
