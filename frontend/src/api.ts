/**
 * Typed wrappers around the backend REST API.
 *
 * Conventions:
 * - All paths are relative to `/api` so the same code works in dev
 *   (Vite proxies `/api` to the backend) and in production (nginx
 *   proxies `/api` to the backend, see `frontend/nginx.conf`).
 * - Authentication is delivered as an httpOnly cookie set by
 *   the backend on signup / login and cleared on logout. JS on
 *   the page cannot read the token, which means a stored-XSS
 *   bug cannot exfiltrate the session. `fetch` sends the cookie
 *   automatically because the request is same-origin; the
 *   `credentials: "include"` option below makes that explicit
 *   for the test environment where the Vite proxy still sees
 *   the cookie.
 * - Domain errors are normalized into {@link ApiError} so callers
 *   can branch on `error.code` without inspecting `Response` objects.
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
  WorkshopDetail,
  WorkshopListEnvelope,
} from "./types";

const API_BASE = "/api";

/**
 * Normalized error type for every API failure.
 *
 * The HTTP status, the backend `error.code`, and the human message
 * are all exposed for callers that need to branch.
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

/**
 * Convert a non-OK `Response` into an {@link ApiError}.
 *
 * Handles both shapes the backend can return: the standard error
 * envelope (`{"error": {...}}`) and FastAPI's default validation
 * payload (`{"detail": [...]}`).
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

/** Execute an API request with consistent error and JSON handling. */
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

/** Payload for creating a workshop session. */
export interface WorkshopCreatePayload {
  title: string;
  /** ISO-8601 timestamp; timezone-aware. */
  starts_at: string;
  /** ISO-8601 timestamp, or `null` when the session has no fixed end. */
  ends_at?: string | null;
  max_capacity: number;
  description?: string;
  category?: string;
  location?: string;
  /**
   * ISO-8601 booking deadline, or `null` to close
   * registration when the session starts.
   */
  registration_closes_at?: string | null;
  /**
   * Owning organization. When set, the caller must
   * be an administrator or a member of that
   * organization; omitted (or `null`) leaves the
   * session platform-managed.
   */
  organization_id?: string | null;
}

/**
 * Payload for editing a workshop. Every field is
 * optional; omitted fields keep their current value.
 */
export interface WorkshopUpdatePayload {
  title?: string;
  starts_at?: string;
  ends_at?: string | null;
  max_capacity?: number;
  description?: string;
  category?: string;
  location?: string;
  registration_closes_at?: string | null;
  /**
   * Reassign the workshop to another organization;
   * the caller must be a member of the new
   * organization. Omitted keeps the current
   * ownership.
   */
  organization_id?: string | null;
}

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
export async function listWorkshops(
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
}

/** Create a workshop session as the configured administrator. */
export async function createWorkshop(payload: WorkshopCreatePayload): Promise<Workshop> {
  return request<Workshop>("/workshops", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Edit a workshop's mutable fields as the configured administrator. */
export async function updateWorkshop(
  id: string,
  payload: WorkshopUpdatePayload,
): Promise<Workshop> {
  return request<Workshop>(`/workshops/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Publish a draft workshop as the configured administrator. */
export async function publishWorkshop(id: string): Promise<Workshop> {
  return request<Workshop>(`/workshops/${id}/publish`, { method: "POST" });
}

/**
 * Cancel a workshop as the configured administrator.
 *
 * Refused with 409 while active reservations exist.
 */
export async function cancelWorkshop(id: string): Promise<Workshop> {
  return request<Workshop>(`/workshops/${id}/cancel`, { method: "POST" });
}

/** Cancel an unbooked workshop session as the configured administrator. */
export async function deleteWorkshop(id: string): Promise<void> {
  return request<void>(`/workshops/${id}`, {
    method: "DELETE",
  });
}

/**
 * Fetch the organizer dashboard statistics (`GET /api/organizer/stats`).
 *
 * The payload aggregates over the workshops the caller may
 * manage: every workshop for the configured super-admin, or
 * the workshops owned by the caller's organizations. It
 * carries the total active bookings, the count of upcoming
 * published sessions, and a per-workshop breakdown with
 * booking counts and the full attendee list (including
 * booking codes).
 *
 * @returns The organizer dashboard payload.
 */
export async function getOrganizerStats(): Promise<OrganizerDashboardResponse> {
  return request<OrganizerDashboardResponse>("/organizer/stats");
}

/**
 * Fetch a single workshop (`GET /api/workshops/{id}`).
 *
 * The session cookie is sent automatically; signed-in callers see
 * their own reservations in the embedded list while anonymous
 * callers always see an empty list. The caller's 1-based
 * `waitlist_position` is included when they hold a place in line.
 */
export async function getWorkshop(id: string): Promise<WorkshopDetail> {
  return request<WorkshopDetail>(`/workshops/${id}`);
}

/**
 * Review a past workshop session (`POST /api/workshops/{id}/reviews`).
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
export async function createReview(
  workshopId: string,
  rating: number,
  text: string,
): Promise<Review> {
  return request<Review>(`/workshops/${workshopId}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating, text }),
  });
}

/**
 * Join a workshop's waitlist (`POST /api/workshops/{id}/waitlist`).
 *
 * Requires a signed-in account. Joining is idempotent: a caller
 * who already holds an active place in line gets that entry back
 * with `replayed=true`.
 *
 * @param workshopId - Workshop to join the waitlist for.
 * @returns The waitlist entry and the caller's queue position.
 */
export async function joinWaitlist(workshopId: string): Promise<WaitlistJoinResponse> {
  return request<WaitlistJoinResponse>(`/workshops/${workshopId}/waitlist`, {
    method: "POST",
  });
}

/**
 * Create a reservation on a workshop.
 *
 * Sends the `Idempotency-Key` header so a network retry replays the
 * original 201 instead of creating a duplicate. The same key is
 * reused across retries; generate a fresh one only when the user
 * explicitly re-submits the form.
 */
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

/** Cancel a reservation. Idempotent; a second call returns 200. */
export async function cancelReservation(reservationId: string): Promise<void> {
  return request<void>(`/reservations/${reservationId}`, {
    method: "DELETE",
  });
}

/**
 * Fetch a single reservation (`GET /api/reservations/{id}`).
 *
 * Owner-only: the signed-in caller may read only their own
 * reservation (including its booking code); a reservation owned
 * by another account, or an unknown id, is a 404.
 *
 * @param reservationId - Reservation UUID.
 * @returns The caller's reservation.
 */
export async function getReservation(reservationId: string): Promise<Reservation> {
  return request<Reservation>(`/reservations/${reservationId}`);
}

/**
 * Leave a waitlist (`DELETE /api/waitlist/{entry_id}`).
 *
 * Requires a signed-in account that owns the entry. Leaving an
 * already-cancelled or promoted entry is a no-op that returns
 * the row as-is.
 *
 * @param entryId - Waitlist entry UUID.
 * @returns The (now cancelled) waitlist entry.
 */
export async function leaveWaitlist(entryId: string): Promise<WaitlistEntry> {
  return request<WaitlistEntry>(`/waitlist/${entryId}`, {
    method: "DELETE",
  });
}

/** Internal helper for the `auth` endpoints. */
async function authRequest(path: string, body: object): Promise<AuthResponse> {
  return request<AuthResponse>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Create a new account and return a signed-in session. */
export function signup(fullName: string, email: string, password: string): Promise<AuthResponse> {
  return authRequest("/auth/signup", { full_name: fullName, email, password });
}

/** Sign in to an existing account. */
export function login(email: string, password: string): Promise<AuthResponse> {
  return authRequest("/auth/login", { email, password });
}

/** Drop the session cookie. Idempotent; safe to call when signed out. */
export async function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

/** Fetch the signed-in account (used on app load to rehydrate the user). */
export async function getMe(): Promise<User> {
  return request<User>("/auth/me");
}

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
export async function createOrganization(
  payload: OrganizationCreatePayload,
): Promise<Organization> {
  return request<Organization>("/organizations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/**
 * Follow an organization (`POST /api/organizations/{id}/follow`).
 *
 * Requires a signed-in account. Following is idempotent:
 * re-following returns the existing follow (HTTP 200
 * instead of 201). Refused with 409
 * `cannot_follow_own_organization` when the caller is a
 * member of the organization.
 *
 * @param organizationId - Organization to follow.
 * @returns The organization with its refreshed
 *   `followers_count`.
 */
export async function followOrganization(organizationId: string): Promise<Organization> {
  return request<Organization>(`/organizations/${organizationId}/follow`, {
    method: "POST",
  });
}

/**
 * Unfollow an organization (`DELETE /api/organizations/{id}/follow`).
 *
 * Requires a signed-in account. Unfollowing is idempotent:
 * a caller who does not follow the organization still gets
 * a successful response with the (unchanged)
 * `followers_count`.
 *
 * @param organizationId - Organization to unfollow.
 * @returns The organization with its refreshed
 *   `followers_count`.
 */
export async function unfollowOrganization(organizationId: string): Promise<Organization> {
  return request<Organization>(`/organizations/${organizationId}/follow`, {
    method: "DELETE",
  });
}

/** List the signed-in account's reservations with workshop titles. */
export async function listMyReservations(): Promise<MyReservation[]> {
  return request<MyReservation[]>("/reservations/me");
}

/**
 * List the signed-in account's active waitlist entries.
 *
 * Each row carries the parent workshop's title and the
 * caller's 1-based position in its queue.
 *
 * @returns The active places in line, oldest first.
 */
export async function listMyWaitlistEntries(): Promise<MyWaitlistEntry[]> {
  return request<MyWaitlistEntry[]>("/waitlist/me");
}
