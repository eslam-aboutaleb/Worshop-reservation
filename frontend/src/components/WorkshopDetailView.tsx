/**
 * Workshop detail view: hero header, seat-availability card,
 * reservation form, and (for signed-in users) the list of their own
 * active reservations on this workshop with cancel buttons.
 *
 * Data flow:
 * - `useWorkshopDetail` fetches the detail payload when its route,
 *   signed-in user, or relevant live event changes.
 * - When a relevant SSE event lands (the parent forwards the most
 *   recent event as a prop), refresh the detail so the reservation
 *   list stays current.
 * - The displayed spot count prefers the live override passed in
 *   from the parent; falls back to the value from the detail
 *   payload.
 *
 * Cancellation is local-state optimistic: the button is marked as
 * busy, the API is awaited, and the detail payload is then refreshed.
 * Failures surface in the global toast layer without losing the
 * user's scroll position.
 */
import { useState } from "react";

import { useAuth } from "../features/auth/AuthContext";
import { useWorkshopDetail } from "../features/workshops/hooks/useWorkshopDetail";
import type { SSEEvent } from "../types";
import { useToastError } from "./Toast";
import { formatDate, formatTime } from "../utils/formatters";
import { AuthPanel } from "./AuthPanel";
import { ReserveForm } from "./ReserveForm";
import { ConfirmDialog } from "./ConfirmDialog";

interface Props {
  /** Workshop to display. */
  workshopId: string;
  /** Live spot-count override from SSE, or `null` if not yet known. */
  availableSpots: number | null;
  /** Most recent SSE event, so changes for this workshop can be reacted to. */
  liveEvent: SSEEvent | null;
  /** Called when the user navigates back to the list. */
  onBack: () => void;
  /** Called with the new spot count after every detail fetch. */
  onSpotsChanged: (workshopId: string, spots: number) => void;
  /** Returns to the catalogue when the session is cancelled elsewhere. */
  onDeleted: () => void;
}

/**
 * Render the workshop detail view.
 *
 * The view has three visual states: loading (skeleton), error, and
 * loaded. The "fully booked" / "open" branch is driven entirely by
 * the spot count so the UI always agrees with the server.
 *
 * Authentication lives in this view, not in the parent: when an
 * anonymous user tries to reserve, the auth modal is opened in
 * place so the URL, scroll position, and the workshop context are all
 * preserved. Navigating away to `/?auth=sign-in` would throw the
 * user out of the workshop they were looking at.
 */
export function WorkshopDetailView({
  workshopId,
  availableSpots,
  liveEvent,
  onBack,
  onSpotsChanged,
  onDeleted,
}: Props) {
  const { user } = useAuth();
  const { detail, error, setError, busyReservationId, refresh, cancel } = useWorkshopDetail(
    workshopId,
    liveEvent,
    user?.id,
    onSpotsChanged,
    onDeleted,
  );
  useToastError(error, () => setError(null));
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);

  if (!detail) {
    return (
      <div
        id="workshop-detail-loading"
        className="mx-auto min-h-screen max-w-6xl px-5 py-6 sm:px-8"
      >
        <button
          id="workshop-detail-back-button-loading"
          type="button"
          onClick={onBack}
          aria-label="Back to all workshops"
          className="font-semibold text-teal hover:text-coral"
        >
          ← Back to workshops
        </button>
        <div className="mt-12 h-10 w-2/3 animate-pulse rounded-xl bg-sand" />
        <div className="mt-5 h-5 w-1/3 animate-pulse rounded bg-sand" />
      </div>
    );
  }

  const spots = availableSpots ?? detail.available_spots;
  const full = spots === 0;
  const availabilityClass =
    "rounded-full px-3 py-1.5 text-xs font-bold uppercase tracking-wider " +
    (full ? "bg-coral text-white" : "bg-teal-light text-teal");
  return (
    <div id="workshop-detail-view" className="min-h-screen">
      <header
        id="workshop-detail-header"
        className="mx-auto flex max-w-6xl items-center justify-between px-5 py-6 sm:px-8"
      >
        <button
          id="workshop-detail-logo"
          type="button"
          onClick={onBack}
          aria-label="Back to all workshops"
          className="display-font flex items-center gap-2 text-lg font-bold tracking-tight"
        >
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-ink text-lg text-paper">
            W
          </span>
          Workshop
        </button>
        <button
          id="workshop-detail-all-workshops-button"
          type="button"
          onClick={onBack}
          aria-label="Back to all workshops"
          className="text-sm font-semibold text-teal hover:text-coral"
        >
          ← Back to workshops
        </button>
      </header>

      <div
        id="workshop-detail-content"
        className="mx-auto max-w-6xl px-5 pb-20 pt-8 sm:px-8 sm:pt-14"
      >
        <div className="max-w-3xl fade-up">
          <p className="text-sm font-bold uppercase tracking-[.2em] text-coral">Workshop details</p>
          <h1
            id="workshop-detail-title"
            className="display-font mt-4 text-4xl font-bold leading-tight tracking-[-.05em] sm:text-6xl"
          >
            {detail.title}
          </h1>
          <div className="mt-6 flex flex-wrap gap-x-6 gap-y-3 text-sm font-medium text-ink/75">
            <span className="flex items-center gap-2">
              <span aria-hidden="true" className="text-teal">
                ◫
              </span>
              {formatDate(detail.starts_at)}
            </span>
            <span>
              <span className="text-ink/65">Starts at</span> {formatTime(detail.starts_at)}
            </span>
          </div>
        </div>

        <div className="mt-12 grid gap-6 lg:grid-cols-[1.15fr_.85fr] lg:items-start">
          <section
            id="workshop-detail-availability-card"
            className="rounded-[2rem] bg-ink p-6 text-paper shadow-xl shadow-ink/10 sm:p-9"
          >
            <div className="flex items-start justify-between gap-5">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[.18em] text-teal-light">
                  Availability
                </p>
                <p
                  id="workshop-detail-spots"
                  className="display-font mt-3 text-6xl font-bold tracking-[-.06em]"
                >
                  {spots}
                </p>
                <p className="mt-1 text-paper/80">
                  {spots === 1 ? "seat remaining" : "seats remaining"} of {detail.max_capacity}
                </p>
              </div>
              <span id="workshop-detail-status" className={availabilityClass}>
                {full ? "Fully booked" : "Taking reservations"}
              </span>
            </div>
            <div className="mt-8 h-2 overflow-hidden rounded-full bg-white/15">
              <div
                className="h-full rounded-full bg-coral transition-all"
                style={{ width: String(Math.max(5, (spots / detail.max_capacity) * 100)) + "%" }}
              />
            </div>
            <p className="mt-5 text-sm leading-6 text-paper/80">
              Reserve a place, then bring your questions. We keep these sessions small so there is
              room to participate.
            </p>
          </section>

          {full ? (
            <section
              id="workshop-detail-full-card"
              className="rounded-[2rem] border border-soft-edge bg-soft p-6 sm:p-9"
            >
              <p className="text-sm font-bold uppercase tracking-[.18em] text-coral">
                This one is popular
              </p>
              <h2 className="display-font mt-4 text-2xl font-bold">All seats are spoken for.</h2>
              <p className="mt-3 text-sm leading-6 text-ink/75">
                Keep an eye on the calendar. A cancellation will make the opening visible
                immediately.
              </p>
            </section>
          ) : (
            <section
              id="workshop-detail-reserve-card"
              className="rounded-[2rem] border border-line bg-paper/90 p-6 shadow-lg shadow-ink/5 sm:p-9"
            >
              <p className="text-sm font-bold uppercase tracking-[.18em] text-teal">
                Join the room
              </p>
              <h2 className="display-font mt-3 text-2xl font-bold">Save your seat</h2>
              <p className="mt-2 text-sm text-ink/70">
                Create a free account to keep this reservation available on every device.
              </p>
              <div className="mt-6">
                <ReserveForm
                  workshopId={workshopId}
                  onReserved={refresh}
                  onOpenAuth={() => setAuthOpen(true)}
                />
              </div>
            </section>
          )}
        </div>

        <section id="workshop-detail-room" className="mt-14">
          <div className="flex items-end justify-between border-b border-line pb-5">
            <div>
              <p className="text-sm font-bold uppercase tracking-[.18em] text-teal">The room</p>
              <h2 className="display-font mt-2 text-3xl font-bold tracking-tight">
                {user ? "Your reservation" : "Reservation privacy"}
              </h2>
            </div>
            <span className="text-sm text-ink/70">
              {user ? (detail.reservations.length ? "Booked" : "Not booked") : "Private"}
            </span>
          </div>
          {user && detail.reservations.length > 0 && (
            <ul
              id="workshop-detail-reservations"
              className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
            >
              {detail.reservations.map((reservation, index) => (
                <li
                  key={reservation.id}
                  id={`workshop-detail-reservation-${reservation.id}`}
                  className="flex items-center gap-3 rounded-2xl border border-line bg-paper/70 p-4"
                >
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-teal-light font-bold text-teal">
                    {reservation.attendee_name.charAt(0).toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{reservation.attendee_name}</p>
                    <p className="truncate text-xs text-ink/70">{reservation.attendee_email}</p>
                  </div>
                  <button
                    id={`workshop-detail-cancel-button-${reservation.id}`}
                    type="button"
                    disabled={busyReservationId === reservation.id}
                    onClick={() => setPendingCancelId(reservation.id)}
                    className="rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-coral hover:bg-soft disabled:opacity-50"
                  >
                    {busyReservationId === reservation.id ? "Cancelling..." : "Cancel"}
                  </button>
                  <span className="sr-only">Reservation {index + 1}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <ConfirmDialog
          open={pendingCancelId !== null}
          title="Cancel this reservation?"
          description="Your seat will be released and offered to the next person. You can re-reserve anytime if seats are still open."
          confirmLabel="Yes, cancel my seat"
          busy={pendingCancelId !== null && busyReservationId === pendingCancelId}
          onConfirm={async () => {
            if (pendingCancelId) {
              await cancel(pendingCancelId);
              setPendingCancelId(null);
            }
          }}
          onCancel={() => setPendingCancelId(null)}
        />
        {/*
          Mount the AuthPanel locally so an anonymous user who clicks
          "Sign in or create an account" gets the modal in place. The
          form re-renders as the signed-in state as soon as the auth
          context resolves, so no further coordination is needed.
        */}
        <AuthPanel
          open={authOpen}
          onClose={() => setAuthOpen(false)}
          onSignedIn={() => setAuthOpen(false)}
        />
      </div>
    </div>
  );
}
