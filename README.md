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

The project ships two compose files:

- `docker-compose.yml` — production stack. nginx serves the built React bundle on
  `:3000`; the FastAPI app runs behind it on `:8000`; PostgreSQL stores data in
  the named volume `postgres_data`.
- `docker-compose.dev.yml` — development overlay. Merged with the base file via
  `-f`. Uses `Dockerfile.dev` for both services, bind-mounts the source tree so
  uvicorn runs with `--reload` and Vite runs with HMR, and stores the dev
  database in `./.dev-data/postgres` so it is independent of the production
  volume.

### 1. First-time setup

Copy the backend environment file, set a database password and a random
authentication secret of at least 32 characters, then start the stack:

```bash
cp backend/.env.example backend/.env
# Edit backend/.env: set POSTGRES_PASSWORD, DATABASE_URL (use the same password),
# and AUTH_SECRET_KEY to a random 32+ character string.
docker compose up --build -d
```

Open the frontend at <http://localhost:3000>. The API documentation is at
<http://localhost:8000/docs> (ReDoc: <http://localhost:8000/redoc>), and the
health check is at <http://localhost:8000/health>.

Set `ADMIN_EMAIL` in `backend/.env` to the account allowed to manage workshop
sessions. The configured account signs up or signs in normally, then uses the
Admin tools in its account screen to create or cancel unbooked sessions.

The backend runs database migrations and seeds three sample workshops on
startup. Seeding is safe to repeat.

For frontend development outside Docker, copy `frontend/.env.example` to
`frontend/.env`. The frontend environment only contains the Vite development
proxy target; do not put secrets in it.

### 2. Common compose commands

All commands run from the repository root.

```bash
# Start the production stack in the background (builds images if missing).
docker compose up --build -d

# Start in the foreground so logs stream to the terminal (Ctrl-C to stop).
docker compose up --build

# Show running containers for the project.
docker compose ps

# Tail logs for every service (Ctrl-C to stop tailing, services keep running).
docker compose logs -f

# Tail logs for a single service.
docker compose logs -f backend

# Stop the stack without removing images, networks, or the database volume.
docker compose stop
docker compose start    # restart the stopped stack

# Stop and remove containers + the default network, but keep volumes and images.
docker compose down
```

### 3. Development override (live reload)

For source-level development, merge the dev overlay so uvicorn runs with
`--reload`, Vite serves with HMR, and the source tree is bind-mounted:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

The dev stack publishes the Vite dev server on <http://localhost:5173> and the
FastAPI app on <http://localhost:8000>. Database files live under
`./.dev-data/postgres` so you can nuke the dev database without touching the
production one. Frontend edits trigger HMR; backend edits trigger uvicorn's
reload watcher; no rebuild is needed for either.

To stop the dev stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

### 4. Run a single service as a standalone container

The compose file wires services together with healthchecks, shared networks,
and `env_file`. Running a service standalone means you take on those
responsibilities yourself: point the container at the right network, pass the
right env vars, and wait for the database yourself.

#### a. Database only

```bash
docker compose up -d db
```

That's the simplest case — the `db` service has no app dependencies. You can
now `psql` into it or run the backend / frontend against it.

#### b. Backend only (against the compose-managed database)

```bash
# Make sure the database is up.
docker compose up -d db

# Build the production image and run it, attached to the compose network.
docker compose build backend
docker compose run --rm --service-ports backend
```

`--service-ports` is important: it publishes the ports declared in
`docker-compose.yml` (`8000:8000`) so you can reach the API from the host.

To run the dev variant of the backend:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
  --service-ports -u 1000:1000 backend
```

The dev command bind-mounts `./backend/src` and runs `alembic upgrade head &&
python -m src.seed && uvicorn ... --reload`.

#### c. Frontend only (production nginx image)

```bash
docker compose build frontend
docker compose run --rm --service-ports frontend
```

The nginx image listens on port 80 inside the container; `--service-ports`
publishes `3000:80` so the host browser can reach it at
<http://localhost:3000>. Without the backend service running on the same Docker
network, `/api/...` requests from the SPA will fail — start the backend (or
the full stack) first.

#### d. Frontend only (Vite dev server)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
  --service-ports frontend
```

Vite serves on `:5173`. The dev overlay sets `VITE_API_TARGET=http://backend:8000`,
so the backend service must be reachable on the compose network. To make
requests from the host browser succeed, also start the backend container (the
dev overlay publishes `8000:8000`).

### 5. Rebuild the images after a change

Docker images cache layers. Whether a rebuild is required depends on what
changed:

| Change                                            | Rebuild needed?            | How                                                       |
| ------------------------------------------------- | -------------------------- | --------------------------------------------------------- |
| `backend/src/**` (Python source)                  | No — the dev overlay bind-mounts the source; uvicorn reloads on save. For the production image, rebuild with `docker compose build backend`. | `docker compose build backend` (or `--no-cache` to force) |
| `backend/Dockerfile` / `Dockerfile.dev`           | Yes                        | `docker compose build backend --no-cache`                 |
| `backend/pyproject.toml` (dependencies)           | Yes                        | `docker compose build backend --no-cache`                 |
| `backend/migrations/**` (Alembic)                 | No — bind-mounted in dev. The backend applies new migrations on next start (`alembic upgrade head` is the default `CMD`). For prod, just restart the service. | `docker compose restart backend` |
| `frontend/src/**`                                 | No — bind-mounted in dev; Vite HMR. For prod, rebuild.   | `docker compose build frontend`                          |
| `frontend/Dockerfile` / `Dockerfile.dev`          | Yes                        | `docker compose build frontend --no-cache`                |
| `frontend/package.json` (dependencies)            | Yes                        | `docker compose build frontend --no-cache`                |
| `docker-compose.yml` / `docker-compose.dev.yml`   | Yes (config-only)          | `docker compose up -d` re-reads the file and recreates changed containers. |
| `backend/.env`                                    | No — bind-mounted via `env_file`. Restart to re-read.    | `docker compose restart backend`                         |
| `frontend/nginx.conf`                             | Yes                        | `docker compose build frontend --no-cache`                |

Rebuild a single service:

```bash
docker compose build backend          # production image, cached layers
docker compose build backend --no-cache   # force a full rebuild
```

Rebuild every service and recreate the stack:

```bash
docker compose build
docker compose up -d
```

If you changed `docker-compose.yml` itself, recreate the affected containers so
the new config (ports, volumes, env, etc.) takes effect:

```bash
docker compose up -d --force-recreate
```

### 6. Clear database data and start fresh

Database data lives in two distinct places, depending on which stack you are
running. Pick the right one for the data you want to wipe.

#### Production stack (`docker-compose.yml`)

Data is stored in the named Docker volume `postgres_data`. To wipe it:

```bash
# Stop and remove the containers AND the named volume.
docker compose down -v
```

`-v` is the flag that drops the volume; without it, `docker compose down`
keeps the data and a future `docker compose up` reuses it. The next `up` will
re-run migrations and re-seed the three sample workshops on first boot, so you
get a clean slate.

If you only want to wipe the data while keeping the containers running, the
cleanest sequence is:

```bash
docker compose down        # stop and remove containers
docker volume rm workshop-reservation_postgres_data
docker compose up -d       # re-create containers; migrations + seed run on first boot
```

The exact volume name is `<project-directory>_postgres_data`. Confirm with
`docker volume ls | grep postgres_data` if you renamed the project.

#### Development stack (`docker-compose.yml` + `docker-compose.dev.yml`)

The dev overlay bind-mounts `./.dev-data/postgres` so the dev database is
independent of the production volume. To reset it:

```bash
# Stop the dev stack and remove the bind-mounted data directory.
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
rm -rf .dev-data
```

Then start the dev stack again:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Migrations run and three sample workshops are seeded on first boot.

#### Wipe only the data inside a running database (keep the schema)

If you want to drop all rows without recreating the container, exec into the
database and run SQL directly. This is the option that preserves any custom
schema changes you have applied manually:

```bash
docker compose exec db psql \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "TRUNCATE TABLE reservations, workshops, users, idempotency_keys, organizations, organization_memberships, organization_follows, reviews, waitlist_entries, alembic_version RESTART IDENTITY CASCADE;"
```

The table names match the SQLAlchemy models in `backend/libs/*/ws_*/models/`. If you
have added new tables, extend the list. Re-seed afterwards by restarting the
backend (`docker compose restart backend` — its entrypoint runs
`python -m src.seed` after migrations).

## Project Structure

The backend is a monorepo of two extracted packages plus the
application that composes them (see `backend/pyproject.toml`;
both packages are installed editable and imported by their
`ws_*` distribution names):

```text
backend/libs/core/ws_core/             Domain-agnostic toolkit: auth (Argon2id,
                                       JWT, session cookie, rate limiting), DB
                                       engine, error registry, event bus port,
                                       realtime (in-process + Redis) adapters,
                                       app factory
backend/libs/reservation/ws_reservation/  The reservation domain as a plugin:
                                       models, schemas, services, routers, and
                                       the organizer-platform auth gate
backend/src/                           Composition root: settings, the composed
                                       app, seed
backend/migrations/                    Alembic migrations
frontend/src/                          React and TypeScript UI
frontend/src/components/               Shared UI components (views, dialogs)
frontend/src/features/                 Feature-scoped modules (auth, reservations,
                                       workshops)
frontend/packages/types/               @ws/types — generated API types + SSE
                                       event / error code types
frontend/packages/api-client/          @ws/api-client — typed fetch wrapper
frontend/packages/realtime/            @ws/realtime — EventSource hook (SSE)
frontend/packages/ui/                  @ws/ui — shared React UI components
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

## Dummy Password Hash

`DUMMY_PASSWORD_HASH` (in `backend/libs/core/ws_core/auth/password.py`)
mitigates basic timing attacks on unknown-email login attempts: the login
path hashes a dummy password with the same Argon2id hasher when the
account does not exist, so response times do not reveal whether an email
is registered. The dummy hash is derived from the hasher at import time,
so it always matches the Argon2id cost parameters in that module — no
manual regeneration step is needed when the parameters change.

## API Routes

The API is mounted under `/api`. Authentication is via a `workshop_access_token`
httpOnly cookie set by `signup` / `login`; the same JWT is also returned in the
response body for non-browser clients, which send it as `Authorization: Bearer
<token>`.

Auth:

- `POST   /api/auth/signup`
- `POST   /api/auth/login`
- `POST   /api/auth/logout`
- `GET    /api/auth/me`

Workshops:

- `GET    /api/workshops` (public catalogue; `?q=`, `?category=`, `?state=`, `?following=true`)
- `GET    /api/workshops/{id}`
- `GET    /api/workshops/events` (SSE stream of reservation/workshop events)
- `POST   /api/workshops` (admin or organization member)
- `PUT    /api/workshops/{id}` (admin or organization member)
- `POST   /api/workshops/{id}/publish` (admin or organization member)
- `POST   /api/workshops/{id}/cancel` (admin or organization member)
- `DELETE /api/workshops/{id}` (admin or organization member, only when no active reservations exist)
- `POST   /api/workshops/{id}/reservations` (requires `Idempotency-Key` header)
- `POST   /api/workshops/{id}/waitlist` (join the waitlist when the workshop is full)
- `POST   /api/workshops/{id}/reviews` (leave a review after attending)

Reservations:

- `GET    /api/reservations/{id}`
- `DELETE /api/reservations/{id}`
- `GET    /api/reservations/me`
- `GET    /api/waitlist/me`
- `DELETE /api/waitlist/{waitlist_entry_id}`

Organizations (roadmap 2.1+):

- `POST   /api/organizations` (creates the org; the caller becomes owner + organizer)
- `POST   /api/organizations/{id}/follow`
- `DELETE /api/organizations/{id}/follow`
- `GET    /api/organizer/stats` (dashboard for organizers: their organizations, workshops, upcoming reservations)

## Trade-offs & Non-Goals

- **SSE is in-process only.** The realtime bus broadcasts within a
  single backend process. Behind a load balancer with multiple
  replicas, only the replica that handled the create/cancel
  broadcasts. The Redis adapter (`ws_core.realtime.redis`) is the
  drop-in replacement when `REDIS_URL` is set.
- **Capacity is enforced by row locking, not a CHECK constraint.**
  `SELECT ... FOR UPDATE` on the workshop row plus an active-reservation
  count inside the same transaction is the single source of truth; a
  partial unique index on `(workshop_id, attendee_email) WHERE
  status='active'` is the second line of defense.
- **Idempotency is two-layered.** The required `Idempotency-Key`
  header is stored in `idempotency_keys` scoped to `(key,
  workshop_id)` for replay across processes; the partial unique index
  catches a client that lies and changes the email on retry.
- **Tests target the concurrency-critical paths** against a real
  PostgreSQL 16 (the partial index and `FOR UPDATE` semantics are not
  reproducible on SQLite).
- **Intentional UI minimalism:** no router, no state library — one
  page with list / detail / reserve states.

## What I'd Do Next

- Redis pub/sub for SSE across replicas (adapter already exists).
- Cancellation TTL so abandoned seats return to the pool.
- E2E Playwright suite over the Docker stack.
- CI running ruff + prettier + pytest + hadolint on every push.
- Admin management UI for the full workshop lifecycle (draft /
  publish / cancel) beyond the current account-screen tools.
