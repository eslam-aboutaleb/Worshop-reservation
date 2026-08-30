# Workshop Reservations

A small full-stack workshop reservation app with a React frontend, FastAPI backend, and PostgreSQL database.

## Features

- Browse workshops and see remaining capacity.
- Reserve and cancel a seat.
- Sign up and sign in from multiple browsers or devices.
- View personal reservation history.
- See only your own attendee details when signed in; public users see counts only.
- Let the environment-configured administrator create and cancel unbooked workshop sessions.
- Receive reservation changes through Server-Sent Events (SSE).
- Safely handle concurrent reservations without exceeding capacity.
- Safely retry reservation requests with the same `Idempotency-Key`.

## Run With Docker

Copy the backend environment file, set a database password and a random authentication secret of at least 32 characters, then start the stack:

```bash
cp backend/.env.example backend/.env
docker compose up --build -d
```

Open the frontend at <http://localhost:3000>. The API documentation is at <http://localhost:8000/docs> (ReDoc: <http://localhost:8000/redoc>), and the health check is at <http://localhost:8000/health>.

Set `ADMIN_EMAIL` in `backend/.env` to the account allowed to manage workshop sessions. The configured account signs up or signs in normally, then uses the Admin tools in its account screen to create or cancel unbooked sessions.

The backend runs database migrations and seeds three sample workshops on startup. Seeding is safe to repeat.

### Development override (live reload)

For source-level development, merge the dev overlay so uvicorn runs with `--reload`, Vite serves with HMR, and the source tree is bind-mounted:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The dev stack publishes the Vite dev server on <http://localhost:5173> and the FastAPI app on <http://localhost:8000>. Database files live under `./.dev-data/postgres` so you can nuke the dev database without touching the production one.

For frontend development outside Docker, copy `frontend/.env.example` to `frontend/.env`. The frontend environment only contains the Vite development proxy target; do not put secrets in it.

## Project Structure

```text
backend/src/api/routers/   HTTP endpoints grouped by resource (workshops, reservations, users)
backend/src/services/      Reservation and workshop workflows
backend/src/services/*_queries.py  Read-only database queries
backend/src/models/        SQLAlchemy entities (user, workshop, reservation, idempotency_key)
backend/src/schemas/       Pydantic request and response models
backend/src/auth.py        JWT issuance, password hashing, admin gating
backend/src/realtime.py    In-process pub/sub used by the SSE stream
backend/src/configuration/ Settings, database engine, structured logging
backend/migrations/        Alembic migrations
frontend/src/              React and TypeScript UI
frontend/src/components/   Shared UI components (views, dialogs, toasts)
frontend/src/features/     Feature-scoped modules (auth, reservations, workshops)
frontend/src/app/          App-level hooks (routing helpers)
```

## Tests

The backend tests require PostgreSQL because capacity locking and partial unique indexes are database features. A convenience wrapper is provided:

```bash
cd backend
./run_tests.sh
```

Or invoke pytest directly through `uv`:

```bash
cd backend
uv run --with ".[dev]" pytest
```

The frontend can be built with:

```bash
cd frontend
npm install
npm run build
```

## Regenerate the Dummy Password Hash

`_DUMMY_PASSWORD_HASH` mitigates basic timing attacks on unknown-email login attempts. Regenerate it if the Argon2id cost parameters in `backend/src/auth.py` change:

```bash
cd backend
PYTHONPATH=. uv run python -c \
'import secrets; from src.auth import hash_password; print(hash_password(secrets.token_urlsafe(32)))'
```

Replace the existing value of `_DUMMY_PASSWORD_HASH` in `backend/src/auth.py` with the printed `$argon2id$...` value.

## API Routes

The API is mounted under `/api`. Authentication is via a `workshop_access_token`
httpOnly cookie set by `signup` / `login`; the same JWT is also returned in the
response body for non-browser clients, which send it as `Authorization: Bearer
<token>`.

- `GET    /api/workshops`
- `GET    /api/workshops/{id}`
- `POST   /api/workshops` (admin)
- `DELETE /api/workshops/{id}` (admin, only when no active reservations exist)
- `POST   /api/workshops/{id}/reservations` (requires `Idempotency-Key` header)
- `DELETE /api/reservations/{id}`
- `GET    /api/reservations/me`
- `POST   /api/auth/signup`
- `POST   /api/auth/login`
- `POST   /api/auth/logout`
- `GET    /api/auth/me`
- `GET    /api/workshops/events` (Server-Sent Events stream)
