/**
 * Typed REST client for the workshop-reservation API.
 *
 * Conventions:
 * - All paths are relative to the configured `baseUrl`
 *   (default `/api`) so the same code works in dev
 *   (Vite proxies `/api` to the backend) and in
 *   production (nginx proxies `/api` to the backend,
 *   see `frontend/nginx.conf`). Pass an absolute
 *   `baseUrl` to talk to the backend directly.
 * - Authentication is delivered as an httpOnly cookie
 *   set by the backend on signup / login and cleared on
 *   logout. JS on the page cannot read the token, which
 *   means a stored-XSS bug cannot exfiltrate the
 *   session. `fetch` sends the cookie automatically
 *   because the request is same-origin; the
 *   `credentials: "include"` option below makes that
 *   explicit for the test environment where the Vite
 *   proxy still sees the cookie.
 * - Domain errors are normalized into {@link ApiError}
 *   so callers can branch on `error.code` without
 *   inspecting `Response` objects.
 *
 * The client is a factory: `createApiClient(config)`
 * returns endpoint groups (`auth`, `workshops`,
 * `reservations`, `waitlist`, `organizations`) whose
 * function names match the historical module-level
 * exports (`listWorkshops`, `createReservation`, …).
 * `fetchImpl` is injectable so tests can stub the
 * network; the data layer stays hand-rolled hooks with
 * an injectable fetch (no TanStack Query).
 */
import type {
  AuthResponse,
  MyReservation,
  MyWaitlistEntry,
  Organization,
  OrganizationCreatePayload,
  OrganizerDashboardResponse,
  Reservation,
  Review,
  User,
  WaitlistEntry,
  WaitlistJoinResponse,
  Workshop,
  WorkshopCreatePayload,
  WorkshopDetail,
  WorkshopListEnvelope,
  WorkshopUpdatePayload,
} from "@ws/types";

/**
 * Normalized error type for every API failure.
 *
 * The HTTP status, the backend `error.code`, and the
 * human message are all exposed for callers that need
 * to branch. `code` is the stable machine string from
 * the `{"error": {"code", "message"}}` envelope; the
 * known codes are listed by `ApiErrorCode` in
 * `@ws/types` (hand-maintained — the envelope is not
 * part of the OpenAPI document).
 */
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

/** Configuration accepted by {@link createApiClient}. */
export interface ApiClientConfig {
  /**
   * Base URL for every request. Defaults to `"/api"`
   * (same-origin, proxied to the backend in dev and
   * prod). Pass an absolute URL such as
   * `"http://localhost:8000/api"` to bypass the proxy.
   */
  baseUrl?: string;
  /**
   * Fetch implementation. Defaults to `globalThis.fetch`.
   * Inject a stub in tests.
   */
  fetchImpl?: typeof fetch;
}

/** Query parameters accepted by `GET /api/workshops`. */
export interface WorkshopListParams {
  /** Case-insensitive substring matched against title and description. */
  q?: string;
  /** Exact category filter. */
  category?: string;
  /** Upcoming (the default), past, or all sessions. */
  state?: "upcoming" | "past" | "all";
  /**
   * Restrict to workshops from organizations the
   * caller follows. Requires a signed-in account;
   * an anonymous request with `following=true` is
   * a 401 rather than an empty page.
   */
  following?: boolean;
  /** Page size (1..100). */
  limit?: number;
  /** Number of matching rows to skip. */
  offset?: number;
}

/**
 * Convert a non-OK `Response` into an {@link ApiError}.
 *
 * Handles both shapes the backend can return: the
 * standard error envelope (`{"error": {...}}`) and
 * FastAPI's default validation payload
 * (`{"detail": [...]}`).
 *
 * @param response - The failed `Response`.
 * @returns A new `ApiError` describing the failure.
 */
async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as {
      error?: { code: string; message: string };
      detail?: Array<{ loc?: Array<string | number>; msg?: string }> | string;
    };
    const validationMessage = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg ?? "Invalid value").join(". ")
      : body.detail;
    return new ApiError(
      response.status,
      body.error?.code ?? "unknown_error",
      body.error?.message ?? validationMessage ?? `Request failed (${response.status})`,
    );
  } catch {
    return new ApiError(response.status, "unknown_error", `Request failed (${response.status})`);
  }
}

/**
 * Build a typed API client.
 *
 * @param config - Optional `baseUrl` / `fetchImpl`
 *   overrides. Defaults: `baseUrl "/api"`,
 *   `credentials "include"`, `fetchImpl globalThis.fetch`.
 * @returns The endpoint groups described in
 *   {@link ApiClient}.
 */
export function createApiClient(config: ApiClientConfig = {}): ApiClient {
  const baseUrl = config.baseUrl ?? "/api";
  const fetchImpl = config.fetchImpl ?? globalThis.fetch;

  /** Execute an API request with consistent error and JSON handling. */
  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    const response = await fetchImpl(`${baseUrl}${path}`, {
      ...options,
      headers,
      credentials: "include",
    });
    if (!response.ok) throw await parseError(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  /** Internal helper for the `auth` endpoints. */
  async function authRequest(path: string, body: object): Promise<AuthResponse> {
    return request<AuthResponse>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  return {
    auth: {
      /** Create a new account and return a signed-in session. */
      signup(fullName: string, email: string, password: string): Promise<AuthResponse> {
        return authRequest("/auth/signup", { full_name: fullName, email, password });
      },

      /** Sign in to an existing account. */
      login(email: string, password: string): Promise<AuthResponse> {
        return authRequest("/auth/login", { email, password });
      },

      /** Drop the session cookie. Idempotent; safe to call when signed out. */
      async logout(): Promise<void> {
        return request<void>("/auth/logout", { method: "POST" });
      },

      /** Fetch the signed-in account (used on app load to rehydrate the user). */
      async getMe(): Promise<User> {
        return request<User>("/auth/me");
      },
    },

    workshops: {
      /**
       * Fetch the workshop catalogue (`GET /api/workshops`).
       *
       * The response is a paginated envelope; `total` counts
       * every matching workshop so callers can render
       * "load more" and know when the catalogue is exhausted.
       *
       * @param params - Search, filter, and pagination options.
       * @returns The paginated workshop envelope.
       */
      async listWorkshops(
        params: WorkshopListParams = {},
      ): Promise<WorkshopListEnvelope> {
        const query = new URLSearchParams();
        if (params.q) query.set("q", params.q);
        if (params.category) query.set("category", params.category);
        if (params.state) query.set("state", params.state);
        if (params.following) query.set("following", "true");
        if (params.limit !== undefined) query.set("limit", String(params.limit));
        if (params.offset !== undefined) query.set("offset", String(params.offset));
        const suffix = query.toString();
        return request<WorkshopListEnvelope>(`/workshops${suffix ? `?${suffix}` : ""}`);
      },

      /**
       * Fetch a single workshop (`GET /api/workshops/{id}`).
       *
       * The session cookie is sent automatically; signed-in
       * callers see their own reservations in the embedded
       * list while anonymous callers always see an empty
       * list. The caller's 1-based `waitlist_position` is
       * included when they hold a place in line.
       */
      async getWorkshop(id: string): Promise<WorkshopDetail> {
        return request<WorkshopDetail>(`/workshops/${id}`);
      },

      /** Create a workshop session as the configured administrator. */
      async createWorkshop(payload: WorkshopCreatePayload): Promise<Workshop> {
        return request<Workshop>("/workshops", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      },

      /** Edit a workshop's mutable fields as the configured administrator. */
      async updateWorkshop(
        id: string,
        payload: WorkshopUpdatePayload,
      ): Promise<Workshop> {
        return request<Workshop>(`/workshops/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      },

      /** Publish a draft workshop as the configured administrator. */
      async publishWorkshop(id: string): Promise<Workshop> {
        return request<Workshop>(`/workshops/${id}/publish`, { method: "POST" });
      },

      /**
       * Cancel a workshop as the configured administrator.
       *
       * Refused with 409 while active reservations exist.
       */
      async cancelWorkshop(id: string): Promise<Workshop> {
        return request<Workshop>(`/workshops/${id}/cancel`, { method: "POST" });
      },

      /** Cancel an unbooked workshop session as the configured administrator. */
      async deleteWorkshop(id: string): Promise<void> {
        return request<void>(`/workshops/${id}`, {
          method: "DELETE",
        });
      },

      /**
       * Fetch the organizer dashboard statistics
       * (`GET /api/organizer/stats`).
       *
       * The payload aggregates over the workshops the caller
       * may manage: every workshop for the configured
       * super-admin, or the workshops owned by the caller's
       * organizations. It carries the total active bookings,
       * the count of upcoming published sessions, and a
       * per-workshop breakdown with booking counts and the
       * full attendee list (including booking codes).
       *
       * @returns The organizer dashboard payload.
       */
      async getOrganizerStats(): Promise<OrganizerDashboardResponse> {
        return request<OrganizerDashboardResponse>("/organizer/stats");
      },

      /**
       * Review a past workshop session
       * (`POST /api/workshops/{id}/reviews`).
       *
       * Open to any signed-in account, but the service layer
       * only accepts it from callers who held a reservation
       * for the workshop (any status) and whose session has
       * ended. One review per user per workshop: a second
       * review is a 409 `review_already_exists`.
       *
       * @param workshopId - Workshop being reviewed.
       * @param rating - Integer rating in 1..5.
       * @param text - Optional comment (max 4000 chars).
       * @returns The created review.
       */
      async createReview(
        workshopId: string,
        rating: number,
        text: string,
      ): Promise<Review> {
        return request<Review>(`/workshops/${workshopId}/reviews`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rating, text }),
        });
      },
    },

    reservations: {
      /**
       * Create a reservation on a workshop.
       *
       * Sends the `Idempotency-Key` header so a network
       * retry replays the original 201 instead of creating
       * a duplicate. The same key is reused across retries;
       * generate a fresh one only when the user explicitly
       * re-submits the form.
       */
      async createReservation(
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
      },

      /** Cancel a reservation. Idempotent; a second call returns 200. */
      async cancelReservation(reservationId: string): Promise<void> {
        return request<void>(`/reservations/${reservationId}`, {
          method: "DELETE",
        });
      },

      /**
       * Fetch a single reservation (`GET /api/reservations/{id}`).
       *
       * Owner-only: the signed-in caller may read only their
       * own reservation (including its booking code); a
       * reservation owned by another account, or an unknown
       * id, is a 404.
       *
       * @param reservationId - Reservation UUID.
       * @returns The caller's reservation.
       */
      async getReservation(reservationId: string): Promise<Reservation> {
        return request<Reservation>(`/reservations/${reservationId}`);
      },

      /** List the signed-in account's reservations with workshop titles. */
      async listMyReservations(): Promise<MyReservation[]> {
        return request<MyReservation[]>("/reservations/me");
      },
    },

    waitlist: {
      /**
       * Join a workshop's waitlist
       * (`POST /api/workshops/{id}/waitlist`).
       *
       * Requires a signed-in account. Joining is idempotent:
       * a caller who already holds an active place in line
       * gets that entry back with `replayed=true`.
       *
       * @param workshopId - Workshop to join the waitlist for.
       * @returns The waitlist entry and the caller's queue position.
       */
      async joinWaitlist(workshopId: string): Promise<WaitlistJoinResponse> {
        return request<WaitlistJoinResponse>(`/workshops/${workshopId}/waitlist`, {
          method: "POST",
        });
      },

      /**
       * Leave a waitlist (`DELETE /api/waitlist/{entry_id}`).
       *
       * Requires a signed-in account that owns the entry.
       * Leaving an already-cancelled or promoted entry is a
       * no-op that returns the row as-is.
       *
       * @param entryId - Waitlist entry UUID.
       * @returns The (now cancelled) waitlist entry.
       */
      async leaveWaitlist(entryId: string): Promise<WaitlistEntry> {
        return request<WaitlistEntry>(`/waitlist/${entryId}`, {
          method: "DELETE",
        });
      },

      /**
       * List the signed-in account's active waitlist entries.
       *
       * Each row carries the parent workshop's title and the
       * caller's 1-based position in its queue.
       *
       * @returns The active places in line, oldest first.
       */
      async listMyWaitlistEntries(): Promise<MyWaitlistEntry[]> {
        return request<MyWaitlistEntry[]>("/waitlist/me");
      },
    },

    organizations: {
      /**
       * Create an organization (`POST /api/organizations`).
       *
       * The caller becomes the organization's first `owner`
       * and is promoted to the `organizer` platform role in
       * the same transaction, so the session should be
       * rehydrated (`getMe` + `setSession`) afterwards to
       * reflect the promotion.
       *
       * @param payload - Organization name and optional slug.
       * @returns The new organization with the creator's
       *   `owner` membership.
       */
      async createOrganization(
        payload: OrganizationCreatePayload,
      ): Promise<Organization> {
        return request<Organization>("/organizations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      },

      /**
       * Follow an organization
       * (`POST /api/organizations/{id}/follow`).
       *
       * Requires a signed-in account. Following is
       * idempotent: re-following returns the existing follow
       * (HTTP 200 instead of 201). Refused with 409
       * `cannot_follow_own_organization` when the caller is
       * a member of the organization.
       *
       * @param organizationId - Organization to follow.
       * @returns The organization with its refreshed
       *   `followers_count`.
       */
      async followOrganization(organizationId: string): Promise<Organization> {
        return request<Organization>(`/organizations/${organizationId}/follow`, {
          method: "POST",
        });
      },

      /**
       * Unfollow an organization
       * (`DELETE /api/organizations/{id}/follow`).
       *
       * Requires a signed-in account. Unfollowing is
       * idempotent: a caller who does not follow the
       * organization still gets a successful response with
       * the (unchanged) `followers_count`.
       *
       * @param organizationId - Organization to unfollow.
       * @returns The organization with its refreshed
       *   `followers_count`.
       */
      async unfollowOrganization(organizationId: string): Promise<Organization> {
        return request<Organization>(`/organizations/${organizationId}/follow`, {
          method: "DELETE",
        });
      },
    },
  };
}

/**
 * The typed API client returned by {@link createApiClient}.
 *
 * Endpoint groups mirror the backend's resource families;
 * function names match the historical module-level exports
 * so a call site reads `client.workshops.listWorkshops()`
 * where it once read `listWorkshops()`.
 */
export interface ApiClient {
  /** `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`. */
  auth: {
    signup(fullName: string, email: string, password: string): Promise<AuthResponse>;
    login(email: string, password: string): Promise<AuthResponse>;
    logout(): Promise<void>;
    getMe(): Promise<User>;
  };
  /** Workshop catalogue, detail, admin mutations, organizer stats, reviews. */
  workshops: {
    listWorkshops(params?: WorkshopListParams): Promise<WorkshopListEnvelope>;
    getWorkshop(id: string): Promise<WorkshopDetail>;
    createWorkshop(payload: WorkshopCreatePayload): Promise<Workshop>;
    updateWorkshop(id: string, payload: WorkshopUpdatePayload): Promise<Workshop>;
    publishWorkshop(id: string): Promise<Workshop>;
    cancelWorkshop(id: string): Promise<Workshop>;
    deleteWorkshop(id: string): Promise<void>;
    getOrganizerStats(): Promise<OrganizerDashboardResponse>;
    createReview(workshopId: string, rating: number, text: string): Promise<Review>;
  };
  /** Reservation create / cancel / read / list. */
  reservations: {
    createReservation(
      workshopId: string,
      attendeeName: string,
      attendeeEmail: string,
      idempotencyKey: string,
    ): Promise<Reservation>;
    cancelReservation(reservationId: string): Promise<void>;
    getReservation(reservationId: string): Promise<Reservation>;
    listMyReservations(): Promise<MyReservation[]>;
  };
  /** Waitlist join / leave / list. */
  waitlist: {
    joinWaitlist(workshopId: string): Promise<WaitlistJoinResponse>;
    leaveWaitlist(entryId: string): Promise<WaitlistEntry>;
    listMyWaitlistEntries(): Promise<MyWaitlistEntry[]>;
  };
  /** Organization create / follow / unfollow. */
  organizations: {
    createOrganization(payload: OrganizationCreatePayload): Promise<Organization>;
    followOrganization(organizationId: string): Promise<Organization>;
    unfollowOrganization(organizationId: string): Promise<Organization>;
  };
}
