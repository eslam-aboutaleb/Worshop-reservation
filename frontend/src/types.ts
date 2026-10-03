/**
 * TypeScript surface for the backend API.
 *
 * The shapes here mirror the Pydantic schemas in
 * `backend/src/schemas/`. They are intentionally hand-written rather
 * than generated: the project is small enough that a single source
 * of truth in TS is fine, and a build-time codegen step is avoided.
 *
 * If a backend field changes, update the matching interface here in
 * the same commit so the two stay in lockstep.
 */

/** Lifecycle states a workshop can move through. */
export type WorkshopStatus = "draft" | "published" | "cancelled";

/** A workshop summary returned by `GET /api/workshops`. */
export interface Workshop {
  id: string;
  title: string;
  /** ISO-8601 timestamp; timezone-aware. */
  starts_at: string;
  /** ISO-8601 timestamp; timezone-aware, or `null` when the session has no fixed end. */
  ends_at: string | null;
  max_capacity: number;
  /** Seats not yet reserved; computed by the service layer. */
  available_spots: number;
  /**
   * Booking deadline, or `null` when registration
   * closes when the workshop starts.
   */
  registration_closes_at: string | null;
  status: WorkshopStatus;
  description: string;
  category: string;
  location: string;
  /**
   * Owning organization, or `null` for
   * platform-managed workshops.
   */
  organization_id: string | null;
  created_at: string;
}

/** Compact reservation summary embedded in workshop detail responses. */
export interface ReservationSummary {
  id: string;
  attendee_name: string;
  attendee_email: string;
  created_at: string;
}

/** Workshop detail payload returned by `GET /api/workshops/{id}`. */
export interface WorkshopDetail extends Workshop {
  /**
   * Active reservations visible to the caller. For signed-in users
   * this is filtered to their own bookings; for anonymous callers
   * it is always empty.
   */
  reservations: ReservationSummary[];
  /**
   * The caller's 1-based position in the waitlist, or
   * `null` when they hold no place in line.
   */
  waitlist_position: number | null;
  /**
   * Average review rating (1..5), or `null` when the
   * workshop has no reviews yet.
   */
  rating_average: number | null;
  /** Number of reviews written for the workshop. */
  rating_count: number;
  /** The workshop's reviews, newest first. */
  reviews: WorkshopReview[];
}

/** Full reservation payload returned by the create / cancel endpoints. */
export interface Reservation {
  id: string;
  workshop_id: string;
  attendee_name: string;
  attendee_email: string;
  /**
   * Server-generated confirmation code (`WKS-` + 6 chars).
   * The check-in lookup key and the value printed on the
   * ticket.
   */
  booking_code: string;
  status: "active" | "cancelled";
  created_at: string;
  cancelled_at: string | null;
}

/** Public account fields (never includes `password_hash`). */
export interface User {
  id: string;
  full_name: string;
  email: string;
  created_at: string;
  /**
   * Platform role: `attendee` (the default), `organizer`
   * (may manage workshops), or `admin`. Distinct from
   * `is_admin`, which flags the environment-configured
   * super-admin.
   */
  role: "attendee" | "organizer" | "admin";
  is_admin: boolean;
}

/** Bearer token and account returned by signup / login. */
export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

/** Reservation enriched with the parent workshop's title for the account dashboard. */
export interface MyReservation extends Reservation {
  workshop_title: string;
}

/** A user's membership in an organization. */
export interface OrganizationMembership {
  user_id: string;
  organization_id: string;
  /** `owner` or `member`. */
  role: string;
  /** ISO-8601 timestamp. */
  created_at: string;
}

/**
 * Organization payload returned by the organizations API.
 *
 * `membership` is the requesting user's membership: populated
 * on creation (the creator becomes the owner), `null` for
 * plain reads that do not resolve a membership.
 */
export interface Organization {
  id: string;
  name: string;
  /** Unique URL-safe identifier. */
  slug: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  membership: OrganizationMembership | null;
  /** Number of accounts following the organization. */
  followers_count: number;
}

/** Payload for creating an organization. */
export interface OrganizationCreatePayload {
  /** Human-readable organization name (1..200 chars). */
  name: string;
  /**
   * Optional URL-safe identifier; derived from `name`
   * when omitted.
   */
  slug?: string;
}

/** A review as returned by the review write endpoint. */
export interface Review {
  id: string;
  workshop_id: string;
  user_id: string;
  /** Integer rating in 1..5. */
  rating: number;
  text: string;
  /** ISO-8601 timestamp. */
  created_at: string;
}

/**
 * Review enriched with the reviewer's display name, as
 * embedded in the workshop detail payload.
 */
export interface WorkshopReview extends Review {
  user_name: string;
}

/**
 * Paginated list envelope returned by `GET /api/workshops`.
 *
 * `total` counts every matching workshop (before
 * pagination) so clients can render "load more" and
 * know when the catalogue is exhausted.
 */
export interface WorkshopListEnvelope {
  items: Workshop[];
  total: number;
  limit: number;
  offset: number;
}

/** A place in line on a workshop's waitlist. */
export interface WaitlistEntry {
  id: string;
  workshop_id: string;
  /** `active` / `promoted` / `cancelled`. */
  status: string;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp, or `null` when not yet promoted. */
  promoted_at: string | null;
}

/** Response for joining a waitlist. */
export interface WaitlistJoinResponse {
  /** The waitlist entry (existing on an idempotent re-join). */
  entry: WaitlistEntry;
  /** The caller's 1-based position in the queue. */
  position: number;
  /**
   * `true` when an existing active entry was returned
   * instead of creating a new one.
   */
  replayed: boolean;
}

/** Waitlist entry enriched with its workshop title and queue position. */
export interface MyWaitlistEntry extends WaitlistEntry {
  /** Title of the parent workshop. */
  workshop_title: string;
  /** The caller's 1-based position in the workshop's queue. */
  position: number;
}

/** Payload published on the `/api/workshops/events` SSE stream.
 *
 * The stream is unauthenticated, so it carries aggregate counts only.
 * It deliberately publishes no reservation identifier: an id is a
 * cancellation capability, and broadcasting one handed every visitor
 * a list of bookings to attack. A client that owns a reservation gets
 * its id from `GET /api/reservations/me`. */
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
 * One reservation on an organizer-managed workshop, as
 * returned by the organizer dashboard.
 */
export interface OrganizerAttendeeSummary {
  /** Reservation UUID. */
  reservation_id: string;
  /** Attendee's full name. */
  attendee_name: string;
  /** Attendee's email. */
  attendee_email: string;
  /**
   * Server-generated confirmation code (`WKS-` + 6 chars);
   * the check-in lookup key.
   */
  booking_code: string;
  /** `active` or `cancelled`. */
  status: string;
  /** ISO-8601 timestamp. */
  created_at: string;
}

/** Per-workshop breakdown on the organizer dashboard. */
export interface OrganizerWorkshopStats {
  /** Workshop UUID. */
  workshop_id: string;
  /** Human-readable title. */
  title: string;
  /** ISO-8601 timestamp; timezone-aware. */
  starts_at: string;
  /** ISO-8601 timestamp, or `null` when the session has no fixed end. */
  ends_at: string | null;
  /** Lifecycle state (`draft` / `published` / `cancelled`). */
  status: string;
  /** Number of active reservations. */
  booking_count: number;
  /**
   * Every reservation ever made for the workshop (any
   * status), oldest first.
   */
  attendees: OrganizerAttendeeSummary[];
}

/** Payload returned by `GET /api/organizer/stats`. */
export interface OrganizerDashboardResponse {
  /** Total active reservations across the caller's workshops. */
  total_bookings: number;
  /** Count of the caller's published workshops that have not started yet. */
  upcoming_sessions: number;
  /** The caller's workshops ordered by `starts_at` ascending. */
  workshops: OrganizerWorkshopStats[];
}
