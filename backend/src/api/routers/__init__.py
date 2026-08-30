"""Application HTTP routers grouped by resource.

Each sub-module declares an ``APIRouter`` for one resource
(``workshops``, ``reservations``, ``users``). This ``__init__``
composes them under a single ``router`` so :mod:`src.main` only has
to mount one thing.

Conventions
-----------

* Routers must not contain business logic. They validate input via
  Pydantic schemas, call into :mod:`src.services`, and shape the
  response. Anything that needs an explicit transaction or a row
  lock belongs in the service layer.
* Routers must not catch domain exceptions raised by the service
  layer; the handlers in :mod:`src.exceptions` turn them into JSON
  responses.
"""

from fastapi import APIRouter

from src.api.routers import reservations, users, workshops

router = APIRouter()
router.include_router(users.router)
router.include_router(workshops.router)
router.include_router(reservations.router)

__all__ = ["router"]
