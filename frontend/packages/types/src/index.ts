/**
 * TypeScript surface for the workshop-reservation API.
 *
 * Two kinds of types live here:
 *
 * 1. **Generated** (`./openapi.d.ts`) — the response and
 *    request schemas, produced by `openapi-typescript`
 *    from the backend's OpenAPI document
 *    (`npm run generate:types`, which fetches
 *    `http://localhost:8000/openapi.json`). NEVER
 *    hand-edit `openapi.d.ts`; regenerate it when the
 *    backend contract changes. The generated names carry
 *    the backend's `*Response` suffix (`WorkshopResponse`,
 *    …); the aliases below expose them under the short
 *    names the app has always used (`Workshop`, …).
 *
 * 2. **Hand-maintained** (`SSEEvent`, `ApiErrorCode`) —
 *    the SSE event payload and the API error-code union
 *    are NOT part of the OpenAPI document (the SSE stream
 *    is an unauthenticated text channel and the error
 *    envelope is a cross-cutting concern), so they are
 *    kept by hand in this file. When the backend adds an
 *    event type or an error code, update them here in the
 *    same commit. Backend sources of truth:
 *    - event types: `backend/libs/reservation/ws_reservation/events.py`
 *    - error codes: `backend/libs/core/ws_core/errors/__init__.py`
 *
 * Generated-shape notes (resolved at call sites, never by
 * editing the generated file):
 * - Fields with server-side defaults (e.g. `status`,
 *   `booking_code`, `reviews`, `workshops`, `attendees`,
 *   `followers_count`) are OPTIONAL in the generated types
 *   even though the backend always sends them; consumers
 *   that require them should coalesce (`detail.reviews ?? []`).
 * - `format: "date-time"` fields are plain `string`s.
 */
import type { components, operations, paths } from "./openapi.d.ts";

export type { components, operations, paths };

/* ------------------------------------------------------------------ */
/* Response types (aliases of the generated `*Response` schemas)      */
/* ------------------------------------------------------------------ */

/** A workshop summary returned by `GET /api/workshops`. */
export type Workshop = components["schemas"]["WorkshopResponse"];

/** Workshop detail payload returned by `GET /api/workshops/{id}`. */
export type WorkshopDetail = components["schemas"]["WorkshopDetailResponse"];

/**
 * Paginated list envelope returned by `GET /api/workshops`.
 *
 * `total` counts every matching workshop (before
 * pagination) so clients can render "load more" and
 * know when the catalogue is exhausted.
 */
export type WorkshopListEnvelope = components["schemas"]["WorkshopListResponse"];

/** Compact reservation summary embedded in workshop detail responses. */
export type ReservationSummary = components["schemas"]["ReservationSummary"];

/** Full reservation payload returned by the create / cancel endpoints. */
export type Reservation = components["schemas"]["ReservationResponse"];

/** Public account fields (never includes `password_hash`). */
export type User = components["schemas"]["UserResponse"];

/** Bearer token and account returned by signup / login. */
export type AuthResponse = components["schemas"]["AuthResponse"];

/** Reservation enriched with the parent workshop's title for the account dashboard. */
export type MyReservation = components["schemas"]["MyReservationResponse"];

/** A user's membership in an organization. */
export type OrganizationMembership =
  components["schemas"]["OrganizationMembershipResponse"];

/**
 * Organization payload returned by the organizations API.
 *
 * `membership` is the requesting user's membership: populated
 * on creation (the creator becomes the owner), `null` for
 * plain reads that do not resolve a membership.
 */
export type Organization = components["schemas"]["OrganizationResponse"];

/** A review as returned by the review write endpoint. */
export type Review = components["schemas"]["ReviewResponse"];

/**
 * Review enriched with the reviewer's display name, as
 * embedded in the workshop detail payload.
 */
export type WorkshopReview = components["schemas"]["WorkshopReviewResponse"];

/** A place in line on a workshop's waitlist. */
export type WaitlistEntry = components["schemas"]["WaitlistEntryResponse"];

/** Response for joining a waitlist. */
export type WaitlistJoinResponse = components["schemas"]["WaitlistJoinResponse"];

/** Waitlist entry enriched with its workshop title and queue position. */
export type MyWaitlistEntry = components["schemas"]["MyWaitlistEntryResponse"];

/**
 * One reservation on an organizer-managed workshop, as
 * returned by the organizer dashboard.
 */
export type OrganizerAttendeeSummary =
  components["schemas"]["OrganizerAttendeeSummary"];

/** Per-workshop breakdown on the organizer dashboard. */
export type OrganizerWorkshopStats =
  components["schemas"]["OrganizerWorkshopStats"];

/** Payload returned by `GET /api/organizer/stats`. */
export type OrganizerDashboardResponse =
  components["schemas"]["OrganizerDashboardResponse"];

/* ------------------------------------------------------------------ */
/* Request payloads (aliases of the generated body schemas)           */
/* ------------------------------------------------------------------ */

/** Payload for creating a workshop session. */
export type WorkshopCreatePayload = components["schemas"]["WorkshopCreate"];

/**
 * Payload for editing a workshop. Every field is
 * optional; omitted fields keep their current value.
 */
export type WorkshopUpdatePayload = components["schemas"]["WorkshopUpdate"];

/** Payload for creating an organization. */
export type OrganizationCreatePayload =
  components["schemas"]["OrganizationCreate"];

/* ------------------------------------------------------------------ */
/* Hand-maintained types (NOT in the OpenAPI document)                */
/* ------------------------------------------------------------------ */

/** Payload published on the `/api/workshops/events` SSE stream.
 *
 * The stream is unauthenticated, so it carries aggregate counts only.
 * It deliberately publishes no reservation identifier: an id is a
 * cancellation capability, and broadcasting one handed every visitor
 * a list of bookings to attack. A client that owns a reservation gets
 * its id from `GET /api/reservations/me`.
 *
 * MAINTAINED BY HAND — sync with the event classes in
 * `backend/libs/reservation/ws_reservation/events.py` when a new
 * domain event is added.
 */
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

/**
 * Machine-readable codes the backend puts in the
 * `{"error": {"code", "message"}}` envelope.
 *
 * MAINTAINED BY HAND — sync with the `DomainError` subclasses in
 * `backend/libs/core/ws_core/errors/__init__.py` when a new error
 * class is added. `unknown_error` is the api-client's fallback for
 * failures that do not carry a recognizable envelope.
 */
export type ApiErrorCode =
  | "workshop_not_found"
  | "reservation_not_found"
  | "workshop_full"
  | "workshop_has_active_reservations"
  | "already_reserved"
  | "registration_closed"
  | "rate_limited"
  | "waitlist_entry_not_found"
  | "email_already_exists"
  | "organization_not_found"
  | "invalid_credentials"
  | "cannot_follow_own_organization"
  | "review_not_eligible"
  | "review_already_exists"
  | "unknown_error";
