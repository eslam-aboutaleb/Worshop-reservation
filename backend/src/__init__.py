"""Workshop Reservations backend.

Handover notes for the next on-call engineer
--------------------------------------------

This package implements a FastAPI service that manages workshop
reservations. The service is intentionally small: six top-level
modules, but a few invariants are load-bearing and must be preserved
when extending it.

* **Capacity is enforced by a row lock on `workshops` inside the
  reservation transaction** (``SELECT ... FOR UPDATE`` followed by an
  active-count check and an INSERT). It is *not* enforced by a CHECK
  constraint or a trigger. See ``services/reservation_service.create_reservation``.
* **Two-layer idempotency.** Clients must send an ``Idempotency-Key``
  header. The key is looked up in the ``idempotency_keys`` table first
  and the prior reservation is returned if it matches. The partial unique
  index ``uq_active_reservation`` on
  ``(workshop_id, attendee_email) WHERE status='active'`` is the second
  line of defense: it catches clients that change their email between
  retries.
* **Real-time updates use in-process pub/sub.** SSE clients register
  an ``asyncio.Queue``; ``realtime.publish`` fans events out. This is
  fine for a single-replica deployment. If you ever scale beyond one
  Uvicorn worker, swap ``src/realtime.py`` for a Redis pub/sub backend.
  Nothing else has to change.
* **Auth is self-issued JWTs** (see ``auth.py``). The token format is
  HS256-signed with ``AUTH_SECRET_KEY``. Tokens are valid for
  ``ACCESS_TOKEN_EXPIRE_DAYS``. Endpoints that need the current user
  depend on ``get_current_user``; endpoints that *may* be anonymous
  depend on ``get_optional_user`` and branch on the result.
* **All errors share a single envelope**: ``{"error": {"code": "...",
  "message": "..."}}``. Custom domain exceptions live in
  ``src/exceptions.py``; their handlers are registered in ``main.py``.

Directory map
-------------

* ``main.py``: FastAPI app factory, CORS, exception handlers, ``/health``.
* ``configuration/``: settings (pydantic-settings) and the async DB
  engine / session factory.
* ``models/``: SQLAlchemy 2.0 ORM models. All models inherit from
  ``Base`` (declared in ``models/base.py``).
* ``schemas/``: Pydantic request/response models at the API boundary.
* ``services/``: business logic. Routers must not write SQL directly;
  they call service functions.
* ``api/routers/``: FastAPI routers grouped by resource
  (``workshops``, ``reservations``, ``users``).
* ``realtime.py``: SSE pub/sub.
* ``auth.py``: password hashing and bearer-token helpers.
* ``exceptions.py``: custom domain exceptions and their handlers.
* ``seed.py``: idempotent demo data loader (``python -m src.seed``).
* ``migrations/``: Alembic, applied automatically on container start.

Running locally
---------------

* ``docker compose up --build`` (the database, backend, and frontend
  are all defined in the top-level ``docker-compose.yml``).
* Backend only: ``uvicorn src.main:app --reload``.
* Tests: ``pytest`` (requires a reachable PostgreSQL; see
  ``tests/conftest.py`` for the assumed ``DATABASE_URL``).
"""

__all__ = []
