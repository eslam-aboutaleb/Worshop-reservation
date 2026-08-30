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
  Reservation,
  User,
  Workshop,
  WorkshopDetail,
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

/** Fetch the workshop list (`GET /api/workshops`). */
export async function listWorkshops(): Promise<Workshop[]> {
  return request<Workshop[]>("/workshops");
}

/** Create a workshop session as the configured administrator. */
export async function createWorkshop(
  title: string,
  startsAt: string,
  maxCapacity: number,
): Promise<Workshop> {
  return request<Workshop>("/workshops", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, starts_at: startsAt, max_capacity: maxCapacity }),
  });
}

/** Cancel an unbooked workshop session as the configured administrator. */
export async function deleteWorkshop(id: string): Promise<void> {
  return request<void>(`/workshops/${id}`, {
    method: "DELETE",
  });
}

/**
 * Fetch a single workshop (`GET /api/workshops/{id}`).
 *
 * The session cookie is sent automatically; signed-in callers see
 * their own reservations in the embedded list while anonymous
 * callers always see an empty list.
 */
export async function getWorkshop(id: string): Promise<WorkshopDetail> {
  return request<WorkshopDetail>(`/workshops/${id}`);
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

/** List the signed-in account's reservations with workshop titles. */
export async function listMyReservations(): Promise<MyReservation[]> {
  return request<MyReservation[]>("/reservations/me");
}
