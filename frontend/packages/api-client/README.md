# @ws/api-client

Typed REST client for the workshop-reservation API.

## Usage

```ts
import { createApiClient } from "@ws/api-client";

const client = createApiClient();
// Defaults: baseUrl "/api", credentials "include",
// fetchImpl globalThis.fetch.

const workshops = await client.workshops.listWorkshops({ state: "upcoming" });
```

### Configuration

```ts
const client = createApiClient({
  baseUrl: "http://localhost:8000/api", // bypass the dev proxy
  fetchImpl: myFetch,                      // injectable (tests, logging)
});
```

### Endpoint groups

| Group | Functions |
|-------|-----------|
| `auth` | `signup`, `login`, `logout`, `getMe` |
| `workshops` | `listWorkshops`, `getWorkshop`, `createWorkshop`, `updateWorkshop`, `publishWorkshop`, `cancelWorkshop`, `deleteWorkshop`, `getOrganizerStats`, `createReview` |
| `reservations` | `createReservation`, `cancelReservation`, `getReservation`, `listMyReservations` |
| `waitlist` | `joinWaitlist`, `leaveWaitlist`, `listMyWaitlistEntries` |
| `organizations` | `createOrganization`, `followOrganization`, `unfollowOrganization` |

Function names match the historical module-level exports
(`listWorkshops`, `createReservation`, …). `createReservation`
keeps the `Idempotency-Key` header so a network retry replays
the original 201.

### Errors

Every non-OK response is normalized into `ApiError`
(`status`, `code`, `message`). The `code` values are the
backend's machine-readable error codes — see `ApiErrorCode`
in `@ws/types` (hand-maintained; the error envelope is not
part of the OpenAPI document).

## Singleton pattern (app integration)

**Decision: module-level singleton with an override hook for
tests** (over a React context).

The app creates exactly one client at module scope and exposes
a getter/setter pair:

```ts
// app: src/apiClient.ts
import { createApiClient } from "@ws/api-client";
import type { ApiClient } from "@ws/api-client";

const defaultClient = createApiClient();
let current: ApiClient = defaultClient;

export function getApiClient(): ApiClient {
  return current;
}

/** Test hook: swap in a client built with a stubbed fetchImpl. */
export function setApiClient(client: ApiClient): void {
  current = client;
}
```

Why a singleton over a React context:

- The data layer is hand-rolled hooks (locked decision — no
  TanStack Query). Hooks call `getApiClient().workshops.…`
  at fetch time, so no provider has to wrap the tree and no
  hook needs to consume context on every render.
- A context would force every view to sit under a provider
  and would re-render consumers when the client value
  changes; the client never changes at runtime.
- The setter gives tests (and any non-React consumer) a
  clean seam to inject a `createApiClient({ fetchImpl })`
  stub without touching the component tree.

## HTTP contract

Unchanged by the extraction: routes, status codes, the
`{"error": {"code", "message"}}` envelope, and the
`workshop_access_token` httpOnly cookie behavior (sent via
`credentials: "include"`).
