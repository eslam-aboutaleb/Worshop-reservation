"""Direct validation coverage for the workshop schemas.

The field validators on :class:`WorkshopCreate` and
:class:`WorkshopUpdate` reject blank titles and registration
deadlines that are already past or later than the session
start. The API tests exercise the happy path; these cases
pin the validators directly so the rejection branches are
covered without depending on request serialization.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ws_reservation.schemas.workshop import WorkshopCreate, WorkshopUpdate

_FUTURE_START = datetime.now(UTC) + timedelta(days=30)


def _create_payload(**overrides) -> dict:
    """A valid create payload with per-test overrides."""
    payload = {
        "title": "Schema test session",
        "starts_at": _FUTURE_START.isoformat(),
        "max_capacity": 4,
    }
    payload.update(overrides)
    return payload


def test_create_rejects_blank_title() -> None:
    """A whitespace-only title is rejected after stripping."""
    with pytest.raises(ValidationError, match="Title must not be blank"):
        WorkshopCreate.model_validate(_create_payload(title="   "))


def test_create_strips_title_whitespace() -> None:
    """Leading/trailing title whitespace is normalized."""
    workshop = WorkshopCreate.model_validate(_create_payload(title="  Padded  "))
    assert workshop.title == "Padded"


def test_create_strips_free_text_fields() -> None:
    """Description, category and location are trimmed."""
    workshop = WorkshopCreate.model_validate(
        _create_payload(
            description="  long  ",
            category="  cat  ",
            location="  room  ",
        )
    )
    assert workshop.description == "long"
    assert workshop.category == "cat"
    assert workshop.location == "room"


def test_create_allows_null_registration_deadline() -> None:
    """A ``None`` deadline means "closes when the session starts"."""
    workshop = WorkshopCreate.model_validate(
        _create_payload(registration_closes_at=None)
    )
    assert workshop.registration_closes_at is None


def test_create_rejects_past_registration_deadline() -> None:
    """An explicit deadline in the past would create an
    unbookable session, so it is rejected at the boundary."""
    past = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(ValidationError, match="must not be in the past"):
        WorkshopCreate.model_validate(
            _create_payload(registration_closes_at=past.isoformat())
        )


def test_create_rejects_deadline_after_start() -> None:
    """The deadline must precede the session start."""
    late = _FUTURE_START + timedelta(days=1)
    with pytest.raises(ValidationError, match="must not be after starts_at"):
        WorkshopCreate.model_validate(
            _create_payload(registration_closes_at=late.isoformat())
        )


def test_create_accepts_valid_registration_deadline() -> None:
    """A deadline between now and the start is preserved."""
    deadline = datetime.now(UTC) + timedelta(days=1)
    workshop = WorkshopCreate.model_validate(
        _create_payload(registration_closes_at=deadline.isoformat())
    )
    assert workshop.registration_closes_at is not None


def test_update_allows_null_title() -> None:
    """Omitting the title keeps the current value."""
    assert WorkshopUpdate.model_validate({}).title is None


def test_update_rejects_blank_title() -> None:
    """A whitespace-only title cannot clear the field."""
    with pytest.raises(ValidationError, match="Title must not be blank"):
        WorkshopUpdate.model_validate({"title": "   "})


def test_update_allows_null_free_text_fields() -> None:
    """Omitted free-text fields keep their current value."""
    update = WorkshopUpdate.model_validate(
        {"description": None, "category": None, "location": None}
    )
    assert update.description is None
    assert update.category is None
    assert update.location is None


def test_update_strips_free_text_fields() -> None:
    """Provided free-text fields are trimmed."""
    update = WorkshopUpdate.model_validate(
        {"description":  "  d  ", "category": "  c  ", "location": "  l  "}
    )
    assert update.description == "d"
    assert update.category == "c"
    assert update.location == "l"
