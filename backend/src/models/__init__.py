"""ORM models.

Every model in the application is defined in this subpackage and
re-exported from ``src.models`` for Alembic's autogenerate to discover
them via ``Base.metadata``.

Hierarchy
---------

``Base`` (in ``base.py``)
   The ``DeclarativeBase`` subclass all models inherit from.

``Workshop``        : the event attendees reserve a seat for.
``Reservation``     : a single seat booking, owned optionally by a
                       ``User`` and constrained by the partial unique
                       index ``uq_active_reservation``.
``IdempotencyKey``  : stores ``(key, workshop_id) -> reservation_id``
                       for safe replay of ``POST`` requests.
``User``            : personal account that owns reservations across
                       devices. Passwords are stored as Argon2id hashes.
``WaitlistEntry``   : a place in line for a full workshop, promoted
                       to a reservation inside the cancelling
                       transaction.
``Organization``    : an organizing account that owns workshops.
``OrganizationMembership`` : a user's role (``owner``/``member``)
                        within an ``Organization``; keyed on the
                        ``(user_id, organization_id)`` pair.
``OrganizationFollow`` : a user's follow of an ``Organization``
                        (roadmap 2.3); also keyed on the
                        ``(user_id, organization_id)`` pair.
``Review``          : a post-workshop rating and comment
                        (roadmap 2.4); one per ``(workshop_id,
                        user_id)`` pair.

Adding a new model
------------------

1. Define it in a new file under ``src/models/``.
2. Import it in ``src/models/__init__.py`` so Alembic sees it.
3. Generate a migration (``alembic revision --autogenerate -m "..."``)
   and review the diff. Autogenerate is good but not perfect,
   especially for partial indexes.
"""

from src.models.base import Base
from src.models.idempotency_key import IdempotencyKey
from src.models.organization import (
    Organization,
    OrganizationFollow,
    OrganizationMembership,
)
from src.models.reservation import Reservation
from src.models.review import Review
from src.models.user import User
from src.models.waitlist_entry import WaitlistEntry
from src.models.workshop import Workshop

__all__ = [
    "Base",
    "IdempotencyKey",
    "Organization",
    "OrganizationFollow",
    "OrganizationMembership",
    "Reservation",
    "Review",
    "User",
    "WaitlistEntry",
    "Workshop",
]
