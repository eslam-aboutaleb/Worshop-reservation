"""Tests for domain exceptions and the error envelope.

The exception classes carry a stable ``code`` and an HTTP ``status_code``;
the handler must always produce the same shape. Any new domain error
should be added here so a missing code or status surfaces at test
time.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.exceptions import (
    AlreadyReservedError,
    ReservationNotFoundError,
    WorkshopFullError,
    WorkshopNotFoundError,
    domain_error_handler,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(WorkshopNotFoundError, domain_error_handler)
    app.add_exception_handler(ReservationNotFoundError, domain_error_handler)
    app.add_exception_handler(WorkshopFullError, domain_error_handler)
    app.add_exception_handler(AlreadyReservedError, domain_error_handler)

    @app.get("/raise/not-found")
    async def _raise_not_found() -> None:
        raise WorkshopNotFoundError("11111111-1111-1111-1111-111111111111")

    @app.get("/raise/reservation-not-found")
    async def _raise_reservation_not_found() -> None:
        raise ReservationNotFoundError("22222222-2222-2222-2222-222222222222")

    @app.get("/raise/full")
    async def _raise_full() -> None:
        raise WorkshopFullError("33333333-3333-3333-3333-333333333333")

    @app.get("/raise/already-reserved")
    async def _raise_already_reserved() -> None:
        raise AlreadyReservedError("44444444-4444-4444-4444-444444444444")

    return app


@pytest.fixture
def local_client() -> TestClient:
    return TestClient(_build_app())


def test_workshop_not_found_envelope(local_client: TestClient) -> None:
    response = local_client.get("/raise/not-found")
    assert response.status_code == 404
    body = response.json()
    assert body == {
        "error": {
            "code": "workshop_not_found",
            "message": "Workshop 11111111-1111-1111-1111-111111111111 not found",
        }
    }


def test_reservation_not_found_envelope(local_client: TestClient) -> None:
    response = local_client.get("/raise/reservation-not-found")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "reservation_not_found"
    assert "22222222-2222-2222-2222-222222222222" in body["error"]["message"]


def test_workshop_full_envelope(local_client: TestClient) -> None:
    response = local_client.get("/raise/full")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "workshop_full"


def test_already_reserved_envelope(local_client: TestClient) -> None:
    response = local_client.get("/raise/already-reserved")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "already_reserved"


def test_exception_classes_carry_status_and_code() -> None:
    """Every domain exception has stable attributes the handler relies on."""
    assert WorkshopNotFoundError.status_code == 404
    assert WorkshopNotFoundError.code == "workshop_not_found"
    assert ReservationNotFoundError.status_code == 404
    assert ReservationNotFoundError.code == "reservation_not_found"
    assert WorkshopFullError.status_code == 409
    assert WorkshopFullError.code == "workshop_full"
    assert AlreadyReservedError.status_code == 409
    assert AlreadyReservedError.code == "already_reserved"


def test_exception_messages_include_offending_id() -> None:
    """The message embeds the id so logs are diagnosable without re-parsing."""
    exc = WorkshopNotFoundError("abc")
    assert "abc" in str(exc)
    assert exc.workshop_id == "abc"
