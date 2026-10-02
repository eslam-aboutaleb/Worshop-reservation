# Frontend Manual — Workshop Reservations

This manual explains how the React + TypeScript frontend in `frontend/` is
integrated with the FastAPI backend. It is the companion to the top-level
[README.md](../README.md); read that first for stack-wide instructions.

## Table of Contents

1. [Goals & Boundaries](#goals--boundaries)
2. [Architecture at a Glance](#architecture-at-a-glance)
3. [How the Frontend Talks to the Backend](#how-the-frontend-talks-to-the-backend)
4. [Auth, Sessions, and CSRF](#auth-sessions-and-csrf)
5. [REST API Surface Used by the UI](#rest-api-surface-used-by-the-ui)
6. [Live Updates over Server-Sent Events](#live-updates-over-server-sent-events)
7. [Reservation Creation and Idempotency](#reservation-creation-and-idempotency)
8. [Error Handling Conventions](#error-handling-conventions)
9. [State Management Patterns](#state-management-patterns)
10. [Routing, Real-Time Wiring, and App Boot](#routing-real-time-wiring-and-app-boot)
11. [Local Development and the Vite Proxy](#local-development-and-the-vite-proxy)
12. [Production Wiring (nginx + Docker)](#production-wiring-nginx--docker)
13. [Type Synchronization with the Backend](#type-synchronization-with-the-backend)
14. [Testing the Integration](#testing-the-integration)
15. [Troubleshooting](#troubleshooting)

---

## Goals & Boundaries

- **Thin client.** The UI never holds domain rules (capacity, cancellation
  policy, admin gating). It renders whatever the backend returns and
  forwards intent through [`api.ts`](./src/api.ts).
- **One origin.** Every API call targets `/api/...`. The browser sees a
  same-origin request, which lets the session cookie travel automatically
  and avoids CORS preflights.
- **Type-safe end to end.** TypeScript interfaces in
  [`types.ts`](./src/types.ts) mirror the Pydantic schemas in
  `backend/src/schemas/`. When the backend shape changes, update the TS
  interface in the same commit.
- **Progressive enhancement.** Anonymous users can browse and reserve.
  Signed-in users get richer data (their own reservations on a workshop
  detail page, full dashboard history).

## Architecture at a Glance

```text
frontend/
├── src/
│   ├── api.ts                      Typed wrappers around every REST endpoint
│   ├── types.ts                    Mirrors Pydantic schemas
│   ├── useEventSource.ts           Connects to /api/workshops/events (SSE)
│   ├── App.tsx                     Top-level shell, route switch, SSE fan-out
│   ├── main.tsx                    React root, providers (StrictMode, ErrorBoundary,
│   │                               ToastProvider, AuthProvider)
│   ├── app/
│   │   └── useRoute.ts             Tiny hash/location router
│   ├── components/                 Shared UI: views, dialogs, toasts, error boundary
│   ├── features/
│   │   ├── auth/AuthContext.tsx    Session rehydration + sign-out
│   │   ├── reservations/hooks/     useMyReservations (account dashboard)
│   │   └── workshops/hooks/        useWorkshopList, useWorkshopDetail
│   └── utils/                      formatters, logger
├── vite.config.ts                  Dev server + /api proxy
├── nginx.conf                      Prod reverse proxy with security headers
└── package.json                    React 18, Vite 6, Tailwind 4
```

The frontend has **no global store library**. React component state,
three feature hooks (`useWorkshopList`, `useWorkshopDetail`,
`useMyReservations`), and one React context (`AuthContext`) cover the
whole app.

## How the Frontend Talks to the Backend

All requests go through the `request` helper in
[`src/api.ts`](./src/api.ts:77):

```ts
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
```

Key properties:

- `API_BASE` is hard-coded to `"/api"`. The path is always relative, so
  the same code works in dev (Vite proxies `/api`) and in prod (nginx
  proxies `/api`).
- `credentials: "include"` is explicit so the cookie is always attached.
  For same-origin requests the browser would send it anyway; the option
  is set to keep the contract obvious when the test harness sets a
  different host.
- The helper parses the JSON body for 2xx, returns `undefined` for
  `204 No Content`, and converts any failure into an [`ApiError`](./src/api.ts:36).

### Endpoint wrappers

`api.ts` exposes one named function per endpoint:

| Function                       | HTTP                                       | Backend route                          |
| ------------------------------ | ------------------------------------------ | -------------------------------------- |
| `listWorkshops()`              | `GET`                                      | `/api/workshops`                       |
| `getWorkshop(id)`              | `GET`                                      | `/api/workshops/{id}`                  |
| `createWorkshop(...)`          | `POST`                                     | `/api/workshops`                       |
| `deleteWorkshop(id)`           | `DELETE`                                   | `/api/workshops/{id}`                  |
| `createReservation(...)`       | `POST` + `Idempotency-Key`                 | `/api/workshops/{id}/reservations`     |
| `cancelReservation(id)`        | `DELETE`                                   | `/api/reservations/{id}`               |
| `signup(...)` / `login(...)`   | `POST`                                     | `/api/auth/signup` \| `/api/auth/login`|
| `logout()`                     | `POST`                                     | `/api/auth/logout`                     |
| `getMe()`                      | `GET`                                      | `/api/auth/me`                         |
| `listMyReservations()`         | `GET`                                      | `/api/reservations/me`                 |

All wrappers live in [`src/api.ts`](./src/api.ts). There is intentionally
no generic CRUD helper — each call site reads as one verb and one path.

## Auth, Sessions, and CSRF

The backend issues a JWT and stores it in two places on
`POST /api/auth/signup` and `POST /api/auth/login`:

1. **httpOnly cookie** `workshop_access_token`. JavaScript on the page
   cannot read this cookie, which means a stored-XSS bug cannot
   exfiltrate the session.
2. **Response body** as `access_token`. The frontend does **not** persist
   this; it is provided for non-browser clients. Browser callers just
   drop it because the cookie carries the session.

`fetch` sends the cookie automatically because the request is
same-origin. `credentials: "include"` is set to make the contract
explicit.

### `AuthContext` lifecycle

[`AuthProvider`](./src/features/auth/AuthContext.tsx) rehydrates the
session once on app boot:

```ts
useEffect(() => {
  getMe()
    .then((hydrated) => setUser(hydrated))
    .catch(() => setUser(null))
    .finally(() => setIsRestoring(false));
}, []);
```

- `isRestoring` is `true` while the `GET /api/auth/me` call is in flight.
  `App.tsx` renders a skeleton during this window so the workshop list
  never flashes its "Sign in" button for a user who is actually signed
  in.
- On success, `user` is set and `setSession` becomes a no-op for new
  logins (the context already holds the user).
- On failure, `user` stays `null`; the caller is anonymous.
- `signOut()` calls `POST /api/auth/logout` and unconditionally clears
  local state, even if the network call fails — logging out should never
  be blocked by a flaky connection.

### CSRF posture

The session cookie is `SameSite=Lax`. State-changing endpoints accept
JSON bodies from a same-origin POST, which Lax permits without a CSRF
token. The CSP in `frontend/nginx.conf` (`connect-src 'self'`) prevents
the browser from being tricked into talking to a third party.

## REST API Surface Used by the UI

The full backend surface is listed in the top-level
[README.md](../README.md). The UI uses these patterns on top of it:

### Workshops

- `GET /api/workshops` → list with computed `available_spots`. The list
  hook re-fetches when the SSE stream reports a `workshop_created` or
  `workshop_deleted` event (see `App.tsx`'s `catalogRevision`).
- `GET /api/workshops/{id}` → detail with embedded reservation summary.
  For signed-in users the list is filtered to their own bookings; for
  anonymous users it is always empty. The detail hook refreshes itself
  on every relevant SSE event for that workshop id.
- `POST /api/workshops` and `DELETE /api/workshops/{id}` are admin-only
  and only reachable when the signed-in `User.is_admin` is `true`. The
  `AccountView` exposes the controls; the backend re-checks the admin
  claim, so a forged client UI cannot bypass the gate.

### Reservations

- `POST /api/workshops/{id}/reservations` creates a reservation. The
  client always supplies an `Idempotency-Key` (see [Reservation Creation
  and Idempotency](#reservation-creation-and-idempotency) below).
- `DELETE /api/reservations/{id}` cancels. The endpoint is idempotent;
  a second call returns `200 OK` and is treated as success.
- `GET /api/reservations/me` powers the account dashboard.

## Live Updates over Server-Sent Events

Workshops are shared state — when one user reserves a seat, every
other open tab should see the count drop immediately. The backend
publishes a single global event stream at `/api/workshops/events`,
backed by the in-process pub/sub in
[`backend/src/realtime.py`](../backend/src/realtime.py). The frontend
subscribes once, in `App.tsx`, and fans the events out.

### Hook: `useEventSource`

[`useEventSource`](./src/useEventSource.ts) opens an `EventSource` on
mount and tears it down on unmount:

```ts
useEffect(() => {
  const source = new EventSource("/api/workshops/events");
  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as SSEEvent;
      handlerRef.current(parsed);
    } catch {
      // Ignore malformed events; the next valid event will recover.
    }
  };
  return () => {
    source.close();
  };
}, []);
```

- The handler is stored in a ref so a new function identity on every
  render does not cause the connection to be torn down.
- `EventSource` auto-reconnects on close; the hook does not surface a
  "live vs. offline" indicator — that is the UI's responsibility if
  wanted.
- Malformed payloads are dropped silently; the next valid message
  recovers the stream.

### Event shape

The shape lives in [`types.ts`](./src/types.ts):

```ts
export interface SSEEvent {
  workshop_id: string;
  type: "reservation_created" | "reservation_cancelled" | "workshop_created" | "workshop_deleted";
  available_spots?: number;
  reservation?: { id: string; status: string; ... };
  reservation_id?: string;
}
```

The public broadcast channel **intentionally omits attendee name and
email** for `reservation_created`; only `id` and `status` are sent.
Per-user details still come from `GET /api/workshops/{id}` after
authentication.

### Fan-out in `App.tsx`

`App.tsx` keeps two pieces of live state derived from the stream:

```ts
const [spotOverrides, setSpotOverrides] = useState<Record<string, number>>({});
const [liveEvent, setLiveEvent] = useState<SSEEvent | null>(null);
const [catalogRevision, setCatalogRevision] = useState(0);

const handleEvent = useCallback((event: SSEEvent) => {
  if (event.type === "workshop_created" || event.type === "workshop_deleted") {
    setCatalogRevision((value) => value + 1);
  } else if (event.available_spots !== undefined) {
    setSpotOverrides((previous) => ({
      ...previous,
      [event.workshop_id]: event.available_spots ?? previous[event.workshop_id] ?? 0,
    }));
  }
  setLiveEvent(event);
}, []);
```

- `spotOverrides` overlays the list view's `available_spots` so the
  count updates without a re-fetch.
- `liveEvent` is the **last event received**. The workshop detail view
  receives it as a prop and re-fetches itself when the event's
  `workshop_id` matches the open card. Using "last event" rather than
  "all events since mount" avoids replaying stale events after a route
  change.
- `catalogRevision` is a monotonic counter; `useWorkshopList` depends
  on it so the list re-fetches only when a workshop is created or
  deleted, not on every reservation change.

## Reservation Creation and Idempotency

Network retries are unavoidable on mobile networks. To make
"reservation submitted twice" impossible, the backend requires an
`Idempotency-Key` header on `POST /api/workshops/{id}/reservations`:

```ts
export async function createReservation(
  workshopId: string,
  attendeeName: string,
  attendeeEmail: string,
  idempotencyKey: string,
): Promise<Reservation> {
  return request<Reservation>(`/workshops/${workshopId}/reservations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ attendee_name: attendeeName, attendee_email: attendeeEmail }),
  });
}
```

Conventions used by the call sites:

- **Same key for retries.** The form generates the key once on submit
  and reuses it across retries of the same submit.
- **Fresh key only on explicit re-submit.** When the user clicks
  "Reserve" again after seeing the result, the form generates a new
  key.
- The backend stores the key in the `idempotency_keys` table and
  replays the original 201 response on duplicate submissions. See
  `backend/src/services/reservation_service.py` for the server-side
  contract.

## Error Handling Conventions

Every API failure is normalised into an `ApiError`:

```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
```

`parseError` understands both backend error shapes:

- `{"error": {"code": "...", "message": "..."}}` — the application's
  own envelope, defined in `backend/src/exceptions.py`.
- `{"detail": [...]}` — FastAPI's default Pydantic validation payload.

Callers branch on `error.code` rather than HTTP status when they need
specific UX (e.g. the workshop detail hook checks
`code === "workshop_not_found"` and navigates home):

```ts
if (requestError instanceof ApiError && requestError.code === "workshop_not_found") {
  onDeleted();
  return;
}
```

A React `ErrorBoundary` wraps the whole tree in `main.tsx` and renders
a "Something went wrong" panel for render-time exceptions, so network
failures do not blank the page.

## State Management Patterns

There are three knobs and they are explicit:

1. **Local component state.** Form fields, dialog visibility, transient
   UI flags. Each component owns its own `useState`.
2. **Feature hooks.** A hook owns one resource and the operations on
   it. The hook returns data + error + the mutators; the view consumes
   that without any other glue.
   - [`useWorkshopList(refreshKey)`](./src/features/workshops/hooks/useWorkshopList.ts)
     — fetches the list, sorts by `starts_at` ascending.
   - [`useWorkshopDetail(workshopId, liveEvent, userId, ...)`](./src/features/workshops/hooks/useWorkshopDetail.ts)
     — fetches one workshop, re-fetches on matching SSE event, exposes
     `cancel`.
   - [`useMyReservations()`](./src/features/reservations/hooks/useMyReservations.ts)
     — fetches the dashboard list, exposes `cancel` that updates the
     local row to `cancelled` instead of re-fetching.
3. **`AuthContext`.** Single shared user object plus `isRestoring` and
   `signOut`. Read with `useAuth()` from
   [`AuthContext.tsx`](./src/features/auth/AuthContext.tsx).

There is no Redux, Zustand, or React Query. Hooks + context are enough
for this surface area.

## Routing, Real-Time Wiring, and App Boot

`main.tsx` mounts the tree in this order:

```tsx
<React.StrictMode>
  <ErrorBoundary>
    <ToastProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ToastProvider>
  </ErrorBoundary>
</React.StrictMode>
```

`App.tsx` then:

1. Reads the route from [`useRoute()`](./src/app/useRoute.ts) (a tiny
   history-based router — no `react-router` dependency).
2. Subscribes to the SSE stream once with `useEventSource`.
3. While `isRestoring` is `true`, renders a skeleton instead of the
   list (so the workshop list never flashes its "Sign in" button for a
   signed-in user).
4. Switches on `route.name`:
   - `home` → `WorkshopListView`, passing `spotOverrides` and a
     `refreshKey={catalogRevision}`.
   - `workshop` → `WorkshopDetailView`, passing `availableSpots` for
     that id, the latest `liveEvent`, and callbacks for
     `onSpotsChanged` / `onDeleted` / `onBack`.
   - `account` → `AccountView` (only when `user` is set; otherwise
     navigate home with `?auth=sign-in`).
   - anything else → `NotFoundView`.

Document title is set per route in an effect so screen readers hear
the 404 state without inspecting the URL.

## Local Development and the Vite Proxy

[`vite.config.ts`](./vite.config.ts) proxies two prefixes to the
backend:

```ts
server: {
  host: "0.0.0.0",
  port: 3000,
  proxy: {
    "/api":    { target: apiTarget, changeOrigin: true },
    "/health": { target: apiTarget, changeOrigin: true },
  },
},
```

`apiTarget` defaults to `http://localhost:8000` and can be overridden
via `VITE_API_TARGET` in `frontend/.env`.

Typical workflow:

```bash
# Terminal 1 — backend
cd backend
./run_tests.sh        # or: uv run uvicorn src.main:app --reload

# Terminal 2 — frontend
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. The browser sees `/api/...`, Vite
forwards it to the backend on `:8000`, and the cookie stays same-origin
from the browser's point of view.

The dev Docker stack (top-level `docker-compose.dev.yml`) does the same
thing for you and mounts the source trees so uvicorn reloads and Vite
HMR both work.

## Production Wiring (nginx + Docker)

In production the frontend is served by the nginx image defined in
[`frontend/Dockerfile`](./Dockerfile). nginx is configured by
[`frontend/nginx.conf`](./nginx.conf) to:

1. Serve `index.html` for any unknown path (`try_files $uri $uri/
   /index.html`) so the client-side router works on hard reload.
2. Proxy `/api/workshops`, `/api/workshops/`, and `/api/` to the
   `backend` service on port 8000.
3. Proxy `/health` to the backend for the docker-compose health check.
4. Set strict security headers on every response:
   - `Content-Security-Policy: default-src 'self'; connect-src 'self'; ...`
     — defense-in-depth against stored XSS; the httpOnly cookie is the
     primary protection.
   - `X-Frame-Options: DENY` — no legitimate reason to be framed.
   - `Referrer-Policy: same-origin` — keeps the workshop id in the URL
     from leaking to outbound links.
   - `X-Content-Type-Options: nosniff` — stops MIME sniffing on static
     assets.
   - `Permissions-Policy: geolocation=(), microphone=(), camera=(),
     payment=()` — opt out of unused powerful APIs.

The exact `location = /api/workshops` block is needed because nginx
otherwise redirects that path to a trailing slash, which drops the
frontend port and breaks the request.

## Type Synchronization with the Backend

There is **no codegen**. Types are hand-written in [`types.ts`](./src/types.ts)
to match `backend/src/schemas/`. The doc comment in `types.ts` spells
out the contract:

> The shapes here mirror the Pydantic schemas in `backend/src/schemas/`.
> They are intentionally hand-written rather than generated: the project
> is small enough that a single source of truth in TS is fine, and a
> build-time codegen step is avoided.
>
> If a backend field changes, update the matching interface here in
> the same commit so the two stay in lockstep.

When you change a backend schema:

1. Update the matching `*Schema` in `backend/src/schemas/`.
2. Update the matching `interface` in `frontend/src/types.ts`.
3. Touch the call sites in `frontend/src/api.ts` and the hooks.

## Testing the Integration

- **Unit / type-level.** `npm run build` runs `tsc -b` against the whole
  project, so a schema drift will fail the build.
- **End-to-end.** [`frontend/e2e/run.mjs`](./e2e/run.mjs) drives the
  app against a live backend (Playwright). It covers auth, workshop
  listing, reservation creation, capacity enforcement, cancellation,
  and SSE-driven spot updates. Run it after backend changes to confirm
  the integration is intact.
- **Manual smoke.** With both servers running, sign up, reserve a seat,
  open a second browser as a different user, and watch the seat count
  drop on the first tab without reloading.

## Troubleshooting

| Symptom                                                 | Likely cause                                                                                              |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| "Sign in" button flashes on first load for a signed-in user | `isRestoring` not honoured — check that `App.tsx` still renders the `isRestoring` skeleton early.       |
| Cookie missing in `fetch` calls from tests               | `credentials: "include"` was removed from `request()`; restore it.                                       |
| SSE events never arrive                                  | nginx `proxy_buffering off; proxy_cache off; proxy_read_timeout 86400s;` missing on `/api/workshops/` — the connection times out at 60s without it. |
| 401 on every call                                       | Cookie not set: confirm `Set-Cookie` is httpOnly + `SameSite=Lax` + `Path=/`. Cross-origin POSTs need `Secure` and explicit CORS. |
| Capacity counts out of sync                              | SSE stream broken or a hook ignored the `liveEvent`; check the browser console for connection drops.     |
| Idempotency-Key rejected                                | Key reused across distinct submissions; generate a new one when the user clicks "Reserve" again.          |
| `workshop_not_found` mid-flow                           | Expected: an admin deleted the workshop. The detail hook catches the code and navigates home.            |
| `tsc` fails after a backend change                      | Schema drift. Update `types.ts` (and any consumer) before re-running the build.                          |

For broader stack issues (database, migrations, environment variables),
see the top-level [README.md](../README.md).