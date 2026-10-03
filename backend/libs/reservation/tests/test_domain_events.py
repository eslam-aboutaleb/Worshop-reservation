"""Payload-identity tests for the app domain events.

The SSE wire contract predates the event bus: the
payloads below are the exact dicts the services used
to pass to ``realtime.publish``. Each test constructs
the event and asserts its payload matches that
original shape, so a future edit that changes a
payload fails here before it reaches a browser.
"""

import uuid

from ws_reservation.events import (
    ReservationCancelled,
    ReservationCreated,
    WaitlistCancelled,
    WaitlistJoined,
    WaitlistLeft,
    WaitlistPromoted,
    WorkshopCancelled,
    WorkshopCreated,
    WorkshopDeleted,
    WorkshopPublished,
    WorkshopUpdated,
)


def test_reservation_created_payload() -> None:
    """The richest event: spot count plus the public reservation view."""
    workshop_id = uuid.uuid4()
    event = ReservationCreated(
        workshop_id=workshop_id,
        available_spots=7,
        reservation={"status": "active"},
    )
    assert event.type == "reservation_created"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "reservation_created",
        "available_spots": 7,
        "reservation": {"status": "active"},
    }


def test_reservation_cancelled_payload() -> None:
    workshop_id = uuid.uuid4()
    event = ReservationCancelled(workshop_id=workshop_id, available_spots=12)
    assert event.type == "reservation_cancelled"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "reservation_cancelled",
        "available_spots": 12,
    }


def test_waitlist_promoted_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WaitlistPromoted(workshop_id=workshop_id, available_spots=3)
    assert event.type == "waitlist_promoted"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "waitlist_promoted",
        "available_spots": 3,
    }


def test_waitlist_joined_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WaitlistJoined(workshop_id=workshop_id)
    assert event.type == "waitlist_joined"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "waitlist_joined",
    }


def test_waitlist_left_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WaitlistLeft(workshop_id=workshop_id)
    assert event.type == "waitlist_left"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "waitlist_left",
    }


def test_waitlist_cancelled_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WaitlistCancelled(workshop_id=workshop_id)
    assert event.type == "waitlist_cancelled"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "waitlist_cancelled",
    }


def test_workshop_created_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WorkshopCreated(workshop_id=workshop_id)
    assert event.type == "workshop_created"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "workshop_created",
    }


def test_workshop_updated_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WorkshopUpdated(workshop_id=workshop_id)
    assert event.type == "workshop_updated"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "workshop_updated",
    }


def test_workshop_published_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WorkshopPublished(workshop_id=workshop_id)
    assert event.type == "workshop_published"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "workshop_published",
    }


def test_workshop_cancelled_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WorkshopCancelled(workshop_id=workshop_id)
    assert event.type == "workshop_cancelled"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "workshop_cancelled",
    }


def test_workshop_deleted_payload() -> None:
    workshop_id = uuid.uuid4()
    event = WorkshopDeleted(workshop_id=workshop_id)
    assert event.type == "workshop_deleted"
    assert event.payload == {
        "workshop_id": str(workshop_id),
        "type": "workshop_deleted",
    }
