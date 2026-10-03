/**
 * Tickets view: the signed-in account's reservations as
 * printable check-in tickets.
 *
 * Data flow:
 * - `useMyReservations` fetches `GET /api/reservations/me`
 *   on mount; each row already carries the parent workshop's
 *   title and the server-generated booking code.
 * - Every reservation renders as a ticket card with its
 *   booking code and a matching QR code.
 * - The print button triggers `window.print()`; the
 *   `@media print` rules in `index.css` hide the interactive
 *   chrome and keep each card intact on the page.
 *
 * Cancelled reservations stay listed (badged and dimmed) so
 * the account's full booking history is visible, but they are
 * clearly marked as no longer valid for check-in.
 *
 * Errors surface in the global toast layer.
 */
import { QRCodeSVG } from "qrcode.react";

import { useMyReservations } from "../features/reservations/hooks/useMyReservations";
import { useToastError } from "./Toast";
import { formatDate } from "../utils/formatters";

interface Props {
  /** Called when the user navigates back to the list. */
  onBack: () => void;
}

/**
 * Render the tickets page.
 *
 * The view is self-contained: it only needs the back
 * callback. Loading and error states mirror the other
 * list views (skeleton grid, toast on failure).
 */
export function TicketsView({ onBack }: Props) {
  const { reservations, error, setError, loading } = useMyReservations();
  useToastError(error, () => setError(null));

  return (
    <div id="tickets-view" className="min-h-screen">
      <header
        id="tickets-header"
        className="mx-auto flex max-w-6xl items-center justify-between px-5 py-6 sm:px-8"
      >
        <button
          id="tickets-logo"
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
        <div className="flex items-center gap-3">
          <button
            id="tickets-print-button"
            type="button"
            onClick={() => window.print()}
            className="print-hidden rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-paper transition hover:bg-teal"
          >
            Print tickets
          </button>
          <button
            id="tickets-back-button"
            type="button"
            onClick={onBack}
            aria-label="Back to all workshops"
            className="print-hidden text-sm font-semibold text-teal hover:text-coral"
          >
            ← Back to workshops
          </button>
        </div>
      </header>

      <section id="tickets-content" className="mx-auto max-w-6xl px-5 pb-20 pt-8 sm:px-8">
        <p className="text-sm font-bold uppercase tracking-[.2em] text-coral">My tickets</p>
        <h1 className="display-font mt-4 text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
          Your bookings
        </h1>
        <p className="mt-3 max-w-xl text-sm leading-6 text-ink/70">
          Show the QR code or booking code at the door. Active tickets scan straight from your phone
          screen — or print this page and bring the paper copy.
        </p>
        {loading ? (
          <div
            id="tickets-loading"
            className="mt-8 grid gap-4 md:grid-cols-2"
            aria-label="Loading tickets"
          >
            {[1, 2].map((item) => (
              <div key={item} className="h-64 animate-pulse rounded-3xl bg-sand" />
            ))}
          </div>
        ) : reservations.length === 0 ? (
          <div
            id="tickets-empty"
            className="mt-8 rounded-3xl border border-dashed border-line bg-paper/60 px-6 py-16 text-center"
          >
            <p className="display-font text-2xl font-semibold">No tickets yet.</p>
            <p className="mt-2 text-ink/70">
              Reserve a seat in a workshop and your ticket will appear here.
            </p>
          </div>
        ) : (
          <ul id="tickets-list" className="mt-8 grid gap-4 md:grid-cols-2">
            {reservations.map((reservation) => (
              <li
                key={reservation.id}
                id={`ticket-${reservation.id}`}
                className="ticket-card rounded-3xl border border-line bg-paper/80 p-6"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-bold uppercase tracking-[.18em] text-teal">Ticket</p>
                    <h2 className="display-font mt-2 text-2xl font-bold leading-tight tracking-tight">
                      {reservation.workshop_title}
                    </h2>
                    <p className="mt-1 text-sm text-ink/70">
                      Booked {formatDate(reservation.created_at)}
                    </p>
                  </div>
                  {reservation.status === "cancelled" ? (
                    <span
                      id={`ticket-status-${reservation.id}`}
                      className="shrink-0 rounded-full bg-soft px-3 py-1 text-sm font-semibold text-danger"
                    >
                      Cancelled
                    </span>
                  ) : (
                    <span
                      id={`ticket-status-${reservation.id}`}
                      className="shrink-0 rounded-full bg-teal-light px-3 py-1 text-sm font-semibold text-teal"
                    >
                      Active
                    </span>
                  )}
                </div>
                <div className="mt-5 flex items-center gap-5 border-t border-line pt-5">
                  <QRCodeSVG
                    value={reservation.booking_code}
                    size={140}
                    level="M"
                    title={`QR code for booking ${reservation.booking_code}`}
                  />
                  <div className="min-w-0">
                    <p className="text-xs font-bold uppercase tracking-wider text-ink/60">
                      Booking code
                    </p>
                    <p
                      id={`ticket-code-${reservation.id}`}
                      className="display-font mt-1 text-2xl font-bold tracking-[.15em]"
                    >
                      {reservation.booking_code}
                    </p>
                    <p className="mt-1 truncate text-sm font-semibold text-ink/75">
                      {reservation.attendee_name}
                    </p>
                    <p className="truncate text-xs text-ink/60">{reservation.attendee_email}</p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
