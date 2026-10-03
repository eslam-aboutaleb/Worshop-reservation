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

- **Thin client.** The UI never holds domain rules (capacity,
  cancellation policy, admin gating). It renders whatever the backend returns and
  forwards intent through the typed client in
  [`@ws/api-client`](./packages/api-client) (the app holds one
  module-level singleton in [`apiClient.ts`](./src/apiClient.ts)).
- **One origin.** Every API call targets `/api/...`. The browser sees a
  same-origin request, which lets the session cookie travel automatically
  and avoids CORS preflights.
- **Type-safe end to end.** TypeScript types live in
  [`@ws/types`](./packages/types): the request and response schemas are
  **generated** into `openapi.d.ts` from the backend's OpenAPI document,
  while `SSEEvent` and `ApiErrorCode` (not part of the OpenAPI document)
  are hand-maintained in `src/index.ts`. The generated schemas mirror the
  Pydantic schemas in
  `backend/libs/reservation/ws_reservation/schemas/`. When the backend
  shape changes, regenerate and update the hand-maintained types in the
  same commit.
- **Progressive enhancement.** Anonymous users can browse and reserve.
  Signed-in users get richer data (their own reservations on a workshop
  detail page, full dashboard history).

## Architecture at a Glance

```text
frontend/
├── packages/                       @ws/* npm workspaces (packages/*)
│   ├── types/                      @ws/types — generated openapi.d.ts plus
│   │                               hand-maintained SSEEvent / ApiErrorCode
│   │                               and short-name aliases
│   ├── api-client/                 @ws/api-client — createApiClient factory,
│   │                               grouped endpoint client, ApiError
│   ├── realtime/                   @ws/realtime — createEventStream (vanilla)
│   │                               and @ws/realtime/react (useEventStream)
│   └── ui/                         @ws/ui — ToastProvider, ConfirmDialog,
│                                       ErrorBoundary
├── src/
│   ├── apiClient.ts                Module-level API client singleton
│   │                               (getApiClient / setApiClient)
│   ├── App.tsx                     Top-level shell, route switch, SSE fan-out
│   ├── main.tsx                    React root, providers (StrictMode, ErrorBoundary,
│   │                               ToastProvider, AuthProvider)
│   ├── app/
│   │   └── useRoute.ts             Tiny history-based router
│   ├── components/                 Views: list, detail, account, tickets, admin,
│   │                               organizer, auth panel, reserve form, …
│   ├── features/
│   │   ├── auth/AuthContext.tsx    Session rehydration + sign-out
│   │   ├── reservations/hooks/     useMyReservations, useMyWaitlistEntries
│   │   └── workshops/hooks/        useWorkshopList, useWorkshopDetail
│   └── utils/                      formatters, logger
├── e2e/run.mjs                     Playwright integration suite
├── vite.config.ts                  Dev server + /api proxy
├── nginx.conf                      Prod reverse proxy with security headers
└── package.json                    npm workspaces; React 18, Vite 6, Tailwind 4
```

The frontend has **no global store library**. React component state,
four feature hooks (`useWorkshopList`, `useWorkshopDetail`,
`useMyReservations`, `useMyWaitlistEntries`), and one React context
(`AuthContext`) cover the whole app. Cross-cutting UI primitives
(toasts, confirm dialog, error boundary) come from `@ws/ui`.

## How the Frontend Talks to the Backend

All requests go through the typed client built by
`createApiClient` in
[`@ws/api-client`](./packages/api-client/src/index.ts):

```ts
import { createApiClient } from "@ws/api-client";

const client = createApiClient();
// Defaults: baseUrl "/api", credentials "include",
// fetchImpl globalThis.fetch.

const page = await client.workshops.listWorkshops({ state: "upcoming" });
```

The app holds exactly one client — a module-level singleton in
[`src/apiClient.ts`](./src/apiClient.ts) — chosen over a React context
so hooks call `getApiClient().<group>.<fn>(...)` at fetch time without
a provider wrapping the tree:

```ts
import { createApiClient } from "@ws/api-client";
import type { ApiClient } from "@ws/api-client";

const defaultClient = createApiClient();
let current: ApiClient = defaultClient;

/** The shared client. Call at fetch time, not at module load. */
export function getApiClient(): ApiClient {
  return current;
}

/** Test hook: swap in a client built with a stubbed fetchImpl. */
export function setApiClient(client: ApiClient): void {
  current = client;
}
```

Key properties:

- `baseUrl` defaults to `"/api"`. The path is always relative, so
  the same code works in dev (Vite proxies `/api`) and in prod (nginx
  proxies `/api`). Pass an absolute URL such as
  `"http://localhost:8000/api"` to bypass the proxy.
- `credentials: "include"` is explicit so the cookie is always attached.
  For same-origin requests the browser would send it anyway; the option
  is set to keep the contract obvious when the test harness sets a
  different host.
- `fetchImpl` is injectable (`createApiClient({ fetchImpl })`), so tests
  can stub the network without touching the component tree.
- The client parses the JSON body for 2xx, returns `undefined` for
  `204 No Content`, and converts any failure into an `ApiError` from
  `@ws/api-client`.

### Endpoint wrappers

`@ws/api-client` exposes one grouped client; call sites read
`client.<group>.<fn>(...)`:

| Call                                                | HTTP                       | Backend route                           |
| --------------------------------------------------- | -------------------------- | --------------------------------------- |
| `client.auth.signup(...)` / `client.auth.login(...)`  | `POST`                     | `/api/auth/signup` \| `/api/auth/login` |
| `client.auth.logout()`                              | `POST`                     | `/api/auth/logout`                      |
| `client.auth.getMe()`                               | `GET`                      | `/api/auth/me`                          |
| `client.workshops.listWorkshops(params?)`           | `GET`                      | `/api/workshops`                        |
| `client.workshops.getWorkshop(id)`                  | `GET`                      | `/api/workshops/{id}`                   |
| `client.workshops.createWorkshop(payload)`          | `POST`                     | `/api/workshops`                        |
| `client.workshops.updateWorkshop(id, payload)`        | `PUT`                      | `/api/workshops/{id}`                   |
| `client.workshops.publishWorkshop(id)`              | `POST`                     | `/api/workshops/{id}/publish`           |
| `client.workshops.cancelWorkshop(id)`               | `POST`                     | `/api/workshops/{id}/cancel`            |
| `client.workshops.deleteWorkshop(id)`               | `DELETE`                   | `/api/workshops/{id}`                   |
| `client.workshops.getOrganizerStats()`              | `GET`                      | `/api/organizer/stats`                  |
| `client.workshops.createReview(...)`                | `POST`                     | `/api/workshops/{id}/reviews`           |
| `client.reservations.createReservation(...)`          | `POST` + `Idempotency-Key` | `/api/workshops/{id}/reservations`      |
| `client.reservations.cancelReservation(id)`           | `DELETE`                   | `/api/reservations/{id}`                |
| `client.reservations.getReservation(id)`              | `GET`                      | `/api/reservations/{id}`                |
| `client.reservations.listMyReservations()`            | `GET`                      | `/api/reservations/me`                  |
| `client.waitlist.joinWaitlist(workshopId)`          | `POST`                     | `/api/workshops/{id}/waitlist`          |
| `client.waitlist.leaveWaitlist(entryId)`            | `DELETE`                   | `/api/waitlist/{entryId}`               |
| `client.waitlist.listMyWaitlistEntries()`           | `GET`                      | `/api/waitlist/me`                      |
| `client.organizations.createOrganization(payload)`  | `POST`                     | `/api/organizations`                    |
| `client.organizations.followOrganization(id)`       | `POST`                     | `/api/organizations/{id}/follow`        |
| `client.organizations.unfollowOrganization(id)`     | `DELETE`                   | `/api/organizations/{id}/follow`        |

All wrappers live in [`@ws/api-client`](./packages/api-client/src/index.ts);
the app reaches them through the `getApiClient()` singleton. There is
intentionally no generic CRUD helper — each call site reads as one verb
and one path.

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
session once on app boot through the shared client:

```ts
useEffect(() => {
  getApiClient()
    .auth.getMe()
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
  `AdminView` exposes the controls; the backend re-checks the admin
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
backed by the realtime bus in
[`backend/libs/core/ws_core/realtime/`](../backend/libs/core/ws_core/realtime/)
— an in-process pub/sub by default, with a Redis adapter selected
by a non-empty `redis_url` setting for multi-replica deployments.
The frontend subscribes once, in `App.tsx`, and fans the events
out.

### Hook: `useEventStream`

[`useEventStream`](./packages/realtime/src/react.ts) (the React
binding of `@ws/realtime`, imported from `@ws/realtime/react`)
opens an `EventSource` on mount and tears it down on unmount:

```ts
import { useEventStream } from "@ws/realtime/react";

useEventStream((event) => {
  // filter on event.workshop_id / event.type
});
```

Under the hood the package's framework-agnostic
`createEventStream(url)` (exported from `@ws/realtime`) owns the
`EventSource`; the React binding adds the lifecycle:

- The handler is stored in a ref so a new function identity on every
  render does not cause the connection to be torn down.
- `EventSource` auto-reconnects on close; the hook does not surface a
  "live vs. offline" indicator — that is the UI's responsibility if
  wanted.
- Malformed payloads are dropped silently; the next valid message
  recovers the stream.

### Event shape

The shape lives in [`@ws/types`](./packages/types/src/index.ts)
(hand-maintained — the SSE stream is not part of the OpenAPI
document):

```ts
export interface SSEEvent {
  workshop_id: string;
  type:
    | "reservation_created"
    | "reservation_cancelled"
    | "workshop_created"
    | "workshop_deleted"
    | "workshop_updated"
    | "workshop_published"
    | "workshop_cancelled"
    | "waitlist_joined"
    | "waitlist_left"
    | "waitlist_promoted"
    | "waitlist_cancelled";
  available_spots?: number;
  reservation?: {
    status: string;
  };
}
```

The public broadcast channel **intentionally omits attendee name,
email, and any reservation identifier** — for `reservation_created`
only the reservation `status` is sent. An id is a cancellation
capability, and broadcasting one would hand every visitor a list of
bookings to attack. Per-user details still come from
`GET /api/workshops/{id}` and `GET /api/reservations/me` after
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
`Idempotency-Key` header on `POST /api/workshops/{id}/reservations`.
The caller generates the key; the `@ws/api-client` wrapper
attaches it as the header:

```ts
// The form generates the key once and reuses it across
// retries; the client sends it as the Idempotency-Key header.
const idempotencyKey = useRef(crypto.randomUUID());

const reservation = await getApiClient().reservations.createReservation(
  workshopId,
  user.full_name,
  user.email,
  idempotencyKey.current,
);
```

Conventions used by the call sites:

- **Same key for retries.** The form (`ReserveForm.tsx`) stores
  the key in a `useRef` on first render and reuses it across
  retries of the same submit — a re-render never rotates the key.
- **Fresh key only on explicit re-submit.** The key is rotated
  after a successful 201, so the next time the user clicks
  "Reserve" starts a new logical request.
- The backend stores the key in the `idempotency_keys` table and
  replays the original 201 response on duplicate submissions (the
  partial unique index `uq_active_reservation` on
  `(workshop_id, attendee_email)` is the race-free backstop). See
  [`backend/libs/reservation/ws_reservation/services/reservation_service.py`](../backend/libs/reservation/ws_reservation/services/reservation_service.py)
  for the server-side contract.

## Error Handling Conventions

Every API failure is normalised into the `ApiError` class
from [`@ws/api-client`](./packages/api-client/src/index.ts):

```ts
import { ApiError } from "@ws/api-client";

// ApiError extends Error and exposes:
//   status: number  — the HTTP status
//   code: string    — the backend's machine-readable error code
//   message: string — the human-readable message
```

The client's `parseError` understands both backend error
shapes:

- `{"error": {"code": "...", "message": "..."}}` — the
  application's own envelope, defined by the `DomainError`
  subclasses in
  [`backend/libs/core/ws_core/errors/`](../backend/libs/core/ws_core/errors/).
- `{"detail": [...]}` — FastAPI's default Pydantic validation
  payload.

Callers branch on `error.code` rather than HTTP status when they need
specific UX (e.g. the workshop detail hook checks
`code === "workshop_not_found"` and navigates home):

```ts
if (requestError instanceof ApiError && requestError.code === "workshop_not_found") {
  onDeleted();
  return;
}
```

A React `ErrorBoundary` from `@ws/ui` wraps the whole tree in
`main.tsx` and renders a "Something went wrong" panel for
render-time exceptions, so network failures do not blank the
page.

## State Management Patterns

There are three knobs and they are explicit:

1. **Local component state.** Form fields, dialog visibility, transient
   UI flags. Each component owns its own `useState`.
2. **Feature hooks.** A hook owns one resource and the operations on
   it. The hook returns data + error + the mutators; the view consumes
   that without any other glue. Every hook calls
   `getApiClient().<group>.<fn>(...)` at fetch time.
   - [`useWorkshopList(refreshKey, filters)`](./src/features/workshops/hooks/useWorkshopList.ts)
     — fetches the paginated catalogue (debounced search,
     `loadMore`); the backend orders pages by `starts_at` ascending.
   - [`useWorkshopDetail(workshopId, liveEvent, userId, ...)`](./src/features/workshops/hooks/useWorkshopDetail.ts)
     — fetches one workshop, re-fetches on matching SSE event, exposes
     `cancel`.
   - [`useMyReservations()`](./src/features/reservations/hooks/useMyReservations.ts)
     — fetches the dashboard list, exposes `cancel` that updates the
     local row to `cancelled` instead of re-fetching.
   - [`useMyWaitlistEntries()`](./src/features/reservations/hooks/useMyWaitlistEntries.ts)
     — fetches the signed-in account's active waitlist places.
3. **`AuthContext`.** Single shared user object plus `isRestoring` and
   `signOut`. Read with `useAuth()` from
   [`AuthContext.tsx`](./src/features/auth/AuthContext.tsx).

Shared UI primitives — `ToastProvider` / `useToast`,
`ConfirmDialog`, and `ErrorBoundary` — come from
[`@ws/ui`](./packages/ui).

There is no Redux, Zustand, or React Query. Hooks + context are enough
for this surface area.

## Routing, Real-Time Wiring, and App Boot

`main.tsx` mounts the tree in this order (`ErrorBoundary` and
`ToastProvider` come from `@ws/ui`; the boundary forwards
render-time exceptions to the structured logger in
`src/utils/logger.ts`):

```tsx
import { ErrorBoundary, ToastProvider } from "@ws/ui";

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ErrorBoundary
      onError={(error, errorInfo) =>
        logger.error("React component boundary caught error", {
          error,
          errorInfo,
        })
      }
    >
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
```

`App.tsx` then:

1. Reads the route from [`useRoute()`](./src/app/useRoute.ts) (a tiny
   history-based router — no `react-router` dependency).
2. Subscribes to the SSE stream once with `useEventStream`
   (from `@ws/realtime/react`).
3. While `isRestoring` is `true`, renders a skeleton instead of the
   list (so the workshop list never flashes its "Sign in" button for a
   signed-in user).
4. Guards the signed-in routes: `account` and `tickets` send anonymous
   visitors home with `?auth=sign-in`; `admin` additionally requires
   `user.is_admin`; `organizer` requires the `organizer` (or `admin`)
   platform role.
5. Switches on `route.name`:
   - `home` → `WorkshopListView`, passing `spotOverrides` and a
     `refreshKey={catalogRevision}`.
   - `workshop` → `WorkshopDetailView`, passing `availableSpots` for
     that id, the latest `liveEvent`, and callbacks for
     `onSpotsChanged` / `onDeleted` / `onBack`.
   - `account` → `AccountView` and `tickets` → `TicketsView` (only
     when `user` is set).
   - `admin` → `AdminView` (only when `user.is_admin`).
   - `organizer` → `OrganizerView` (only for the `organizer`/`admin`
     role).
   - `notFound` → `NotFoundView`; any other route falls through to
     the home list.

Document title is set per route in an effect so screen readers hear the
404 state without inspecting the URL.

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

Types **are generated**. [`@ws/types`](./packages/types) runs
`openapi-typescript` against the backend's OpenAPI document:

```sh
# With the backend running on localhost:8000
npm run generate:types
```

That fetches `http://localhost:8000/openapi.json` and writes
`packages/types/src/openapi.d.ts` (the script lives in
`packages/types/package.json`; without a server you can generate
from a saved schema:
`openapi-typescript /path/to/openapi.json -o src/openapi.d.ts`).

The package has a two-part layout:

| File               | Origin                                                                                               | Edit?                            |
| ------------------ | ---------------------------------------------------------------------------------------------------- | -------------------------------- |
| `src/openapi.d.ts` | Generated by `openapi-typescript` from the backend's OpenAPI document                                | **Never hand-edit** — regenerate |
| `src/index.ts`     | Hand-written entry: re-exports the generated schemas under the app's short names (`Workshop`, `Reservation`, …) plus the hand-maintained `SSEEvent` and `ApiErrorCode` | Yes                              |

The generated names carry the backend's `*Response` suffix
(`WorkshopResponse`, …); the aliases in `src/index.ts` expose
them under the short names the app has always used.

When you change a backend schema:

1. Update the matching Pydantic schema in
   `backend/libs/reservation/ws_reservation/schemas/`.
2. Regenerate: `npm run generate:types` (with the backend up).
3. Update the hand-maintained types in
   `packages/types/src/index.ts` (`SSEEvent`, `ApiErrorCode`) in
   the same commit when the change adds an event type or an
   error code, and touch the call sites in `frontend/src/` that
   consume the new shape.

## Testing the Integration

- **Unit / type-level.** `npm run build` builds the four
  workspaces (`@ws/types`, `@ws/api-client`, `@ws/realtime`,
  `@ws/ui`), then runs `tsc -b && vite build` against the app,
  so a schema drift will fail the build.
- **End-to-end.** [`frontend/e2e/run.mjs`](./e2e/run.mjs) drives the
  app against a live backend (Playwright). It covers auth, workshop
  listing, reservation creation, capacity enforcement, cancellation,
  and SSE-driven spot updates. Run it after backend changes to confirm
  the integration is intact.
- **Manual smoke.** With both servers running, sign up, reserve a seat,
  open a second browser as a different user, and watch the seat count
  drop on the first tab without reloading.

## Troubleshooting

| Symptom                                                     | Likely cause                                                                                                                                        |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| "Sign in" button flashes on first load for a signed-in user | `isRestoring` not honoured — check that `App.tsx` still renders the `isRestoring` skeleton early.                                                   |
| Cookie missing in `fetch` calls from tests                  | `credentials: "include"` was removed from the `request` helper in `@ws/api-client`; restore it.                                                       |
| SSE events never arrive                                     | nginx `proxy_buffering off; proxy_cache off; proxy_read_timeout 86400s;` missing on `/api/workshops/` — the connection times out at 60s without it. |
| 401 on every call                                           | Cookie not set: confirm `Set-Cookie` is httpOnly + `SameSite=Lax` + `Path=/`. Cross-origin POSTs need `Secure` and explicit CORS.                   |
| Capacity counts out of sync                                 | SSE stream broken or a hook ignored the `liveEvent`; check the browser console for connection drops.                                                |
| Idempotency-Key rejected                                    | Key reused across distinct submissions; generate a new one when the user clicks "Reserve" again.                                                    |
| `workshop_not_found` mid-flow                               | Expected: an admin deleted the workshop. The detail hook catches the code and navigates home.                                                       |
| `tsc` fails after a backend change                          | Schema drift. Regenerate `@ws/types` (`npm run generate:types`, backend up) and update the hand-maintained types (and any consumer) before re-running the build. |

For broader stack issues (database, migrations, environment variables),
see the top-level [README.md](../README.md).
