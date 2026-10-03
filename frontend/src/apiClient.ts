/**
 * The app's single API client instance.
 *
 * **Decision: module-level singleton with an override
 * hook for tests** (over a React context — see
 * `@ws/api-client` README for the rationale).
 *
 * The data layer is hand-rolled hooks (locked
 * decision: no TanStack Query). Hooks call
 * `getApiClient().<group>.<fn>(...)` at fetch time,
 * so no provider has to wrap the tree and no hook
 * consumes context on every render. The client never
 * changes at runtime in the app; `setApiClient`
 * exists so tests can swap in a client built with a
 * stubbed `fetchImpl`:
 *
 * ```ts
 * setApiClient(createApiClient({ fetchImpl: stub }));
 * // ... run hooks / render
 * setApiClient(createApiClient()); // restore
 * ```
 */
import { createApiClient } from "@ws/api-client";
import type { ApiClient } from "@ws/api-client";

const defaultClient = createApiClient();
let current: ApiClient = defaultClient;

/** The shared client. Call at fetch time, not at module load. */
export function getApiClient(): ApiClient {
  return current;
}

/**
 * Test hook: replace the shared client (e.g. with one
 * built around a stubbed `fetchImpl`). Pass a fresh
 * `createApiClient()` to restore the default.
 */
export function setApiClient(client: ApiClient): void {
  current = client;
}
