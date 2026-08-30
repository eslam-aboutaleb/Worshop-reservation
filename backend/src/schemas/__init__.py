"""Pydantic request/response models at the API boundary.

Two conventions:

* Every request body that contains a user-supplied email uses the
  shared ``NormalizedEmail`` type that lowercases and trims the
  value, so the database's case-insensitive uniqueness
  (``uq_active_reservation`` partial index) just works.
* Every response model that mirrors a SQLAlchemy row declares
  ``model_config = {"from_attributes": True}`` so the router can
  pass ORM instances directly into ``Model.model_validate(...)``.
"""

from src.schemas.auth import AccountCreate, AuthResponse, LoginRequest, UserResponse
from src.schemas.reservation import (
    MyReservationResponse,
    ReservationCancelResponse,
    ReservationCreate,
    ReservationResponse,
)
from src.schemas.workshop import ReservationSummary, WorkshopDetailResponse, WorkshopResponse

__all__ = [
    "AccountCreate",
    "AuthResponse",
    "LoginRequest",
    "MyReservationResponse",
    "ReservationCancelResponse",
    "ReservationCreate",
    "ReservationResponse",
    "ReservationSummary",
    "UserResponse",
    "WorkshopDetailResponse",
    "WorkshopResponse",
]
