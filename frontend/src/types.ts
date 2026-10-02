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

/** A workshop summary returned by `GET /api/workshops`. */
export interface Workshop {
  id: string;
  title: string;
  /** ISO-8601 timestamp; timezone-aware. */
  starts_at: string;
  max_capacity: number;
  /** Seats not yet reserved; computed by the service layer. */
  available_spots: number;
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
}

/** Full reservation payload returned by the create / cancel endpoints. */
export interface Reservation {
  id: string;
  workshop_id: string;
  attendee_name: string;
  attendee_email: string;
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

/** Payload published on the `/api/workshops/events` SSE stream.
 *
 * The stream is unauthenticated, so it carries aggregate counts only.
 * It deliberately publishes no reservation identifier: an id is a
 * cancellation capability, and broadcasting one handed every visitor
 * a list of bookings to attack. A client that owns a reservation gets
 * its id from `GET /api/reservations/me`. */
export interface SSEEvent {
  workshop_id: string;
  type: "reservation_created" | "reservation_cancelled" | "workshop_created" | "workshop_deleted";
  available_spots?: number;
  reservation?: {
    status: string;
  };
}
