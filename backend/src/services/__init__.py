"""Service layer for the reservation domain.

Routers must not write SQL or business logic directly. They call into
the two modules in this subpackage:

* ``workshop_service``   - read-only listing and detail queries.
* ``reservation_service`` - write-side logic for create/cancel/list,
  including the row-locked capacity check that is the central
  correctness invariant of the system.

Every public function in this package takes an ``AsyncSession`` as
its first argument; the routers obtain the session through the
``get_db`` FastAPI dependency.
"""

from src.services import reservation_service, workshop_service

__all__ = ["reservation_service", "workshop_service"]
