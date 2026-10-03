"""Application HTTP routers grouped by resource.

Each sub-module exposes a ``create_router(container)``
factory for one resource (``workshops``,
``reservations``, ``organizations``, ``organizer``).
This ``__init__`` composes them under a single router so
the reservation plugin only has to mount one thing. The
core ``/auth`` router is mounted by the composition root
(it lives in :mod:`ws_core.auth.routers`).

Conventions
-----------

* Routers must not contain business logic. They validate input via
  Pydantic schemas, call into :mod:`ws_reservation.services`, and shape the
  response. Anything that needs an explicit transaction or a row
  lock belongs in the service layer.
* Routers must not catch domain exceptions raised by the service
  layer; the handlers in :mod:`ws_core.errors` turn them into JSON
  responses.
"""

from fastapi import APIRouter
from ws_core.container import Container

from ws_reservation.routers import (
    organizations,
    organizer,
    reservations,
    workshops,
)


def create_router(container: Container) -> APIRouter:
    """Compose every reservation router under one ``APIRouter``.

    Args:
        container: The dependencies the sub-routers bind to.

    Returns:
        A router mounting the workshops, reservations,
        organizations and organizer routes.
    """
    router = APIRouter()
    router.include_router(workshops.create_router(container))
    router.include_router(reservations.create_router(container))
    router.include_router(organizations.create_router(container))
    router.include_router(organizer.create_router(container))
    return router
