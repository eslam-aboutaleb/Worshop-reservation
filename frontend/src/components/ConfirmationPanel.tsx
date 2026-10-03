/**
 * Post-reservation confirmation panel.
 *
 * Shown in place of the reserve form after a successful
 * `createReservation`. The server-generated booking code is
 * the check-in lookup key, so it is rendered as text and
 * encoded in a QR code the attendee can show at the door.
 *
 * The QR value is the booking code itself (e.g.
 * `WKS-AB3D4F`) — the same string the check-in lookup
 * accepts — so a scan of the code resolves directly to the
 * booking.
 */
import { QRCodeSVG } from "qrcode.react";

import type { Reservation } from "../types";

interface Props {
  /** The reservation that was just created. */
  reservation: Reservation;
}

/**
 * Render the booking confirmation with a scannable QR code.
 *
 * The panel is deliberately self-contained: it needs only the
 * created reservation, so it can be dropped into any view that
 * owns a reservation (the detail view's reserve card today,
 * the tickets page tomorrow).
 */
export function ConfirmationPanel({ reservation }: Props) {
  return (
    <div
      id="reserve-confirmation"
      className="rounded-2xl border border-success-edge bg-success-soft p-5 sm:p-6"
    >
      <p className="text-sm font-bold uppercase tracking-[.18em] text-success">Seat reserved</p>
      <h2 className="display-font mt-2 text-2xl font-bold">You&apos;re in.</h2>
      <div className="mt-5 flex items-center gap-5">
        <QRCodeSVG
          value={reservation.booking_code}
          size={120}
          level="M"
          title={`QR code for booking ${reservation.booking_code}`}
        />
        <div className="min-w-0">
          <p className="text-xs font-bold uppercase tracking-wider text-ink/60">Booking code</p>
          <p
            id="reserve-confirmation-code"
            className="display-font mt-1 text-3xl font-bold tracking-[.15em]"
          >
            {reservation.booking_code}
          </p>
          <p className="mt-2 text-sm leading-5 text-ink/75">
            Show this code at the door — it scans straight from your phone screen.
          </p>
        </div>
      </div>
    </div>
  );
}
