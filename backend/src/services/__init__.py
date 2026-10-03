"""Service layer for the reservation domain.

Routers must not write SQL or business logic directly. They call into
the modules in this subpackage:

* ``workshop_service``      - read-only listing and detail queries,
  plus the workshop lifecycle transitions.
* ``reservation_service``   - write-side logic for create/cancel/list,
  including the row-locked capacity check that is the central
  correctness invariant of the system.
* ``organization_service``  - organization creation and membership
  queries (roadmap 2.1).

Every public function in this package takes an ``AsyncSession`` as
its first argument; the routers obtain the session through the
``get_db`` FastAPI dependency.
"""

from src.services import (
    organization_service,
    reservation_service,
    workshop_service,
)

__all__ = ["organization_service", "reservation_service", "workshop_service"]
