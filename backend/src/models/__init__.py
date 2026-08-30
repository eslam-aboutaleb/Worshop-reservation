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
from src.models.reservation import Reservation
from src.models.user import User
from src.models.workshop import Workshop

__all__ = ["Base", "IdempotencyKey", "Reservation", "User", "Workshop"]
