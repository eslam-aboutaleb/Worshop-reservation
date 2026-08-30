/**
 * Account dashboard: lists the signed-in user's reservations
 * (active and cancelled) with their workshop titles, and exposes
 * an inline cancel control for each active entry.
 *
 * Local state is updated optimistically on cancel so the list feels
 * instant; the `cancelled_at` timestamp is faked to "now" client-
 * side because the response body is the cancel ack, not a refetch.
 * The next visit to the dashboard will show the server-side value.
 */
import { useEffect, useState } from "react";

import { createWorkshop, deleteWorkshop, listWorkshops } from "../api";
import { useAuth } from "../features/auth/AuthContext";
import { useMyReservations } from "../features/reservations/hooks/useMyReservations";
import type { Workshop } from "../types";
import { useToast, useToastError } from "./Toast";
import { ConfirmDialog } from "./ConfirmDialog";
import { formatDate } from "../utils/formatters";

interface Props {
  /** Called when the user clicks "Back to workshops". */
  onClose: () => void;
}

/**
 * Render the account dashboard.
 *
 * Fetches the user's reservations on mount, exposes inline cancel,
 * and surfaces API errors through the global toast layer.
 */
export function AccountView({ onClose }: Props) {
  const { user } = useAuth();
  const { reservations, error, setError, loading, cancel } = useMyReservations();
  const { showToast } = useToast();
  useToastError(error, () => setError(null));
  const [workshops, setWorkshops] = useState<Workshop[]>([]);
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [capacity, setCapacity] = useState("10");
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // datetime-local requires the value in the user's local timezone
  // with no offset suffix; "now" is the earliest time the admin
  // should be able to book a session for.
  const earliestStart = (() => {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    return (
      `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
      `T${pad(now.getHours())}:${pad(now.getMinutes())}`
    );
  })();

  useEffect(() => {
    if (!user?.is_admin) return;
    listWorkshops().then(setWorkshops).catch(setError);
  }, [user?.is_admin, setError]);

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    // Capacity: strip anything that is not a digit, then parse.
    // The input is type=number but the browser will still let a
    // paste bring in signs or exponent markers on some platforms;
    // the value is sanitised here so the only thing the server sees
    // is an integer >= 1.
    const capacityDigits = capacity.replace(/\D+/g, "");
    const capacityNumber = Number.parseInt(capacityDigits, 10);
    if (!Number.isFinite(capacityNumber) || capacityNumber < 1) {
      setFormError("Capacity must be a positive whole number.");
      return;
    }

    // Start time: must be a parseable future-or-now timestamp.
    const parsedStart = new Date(startsAt);
    if (Number.isNaN(parsedStart.getTime())) {
      setFormError("Pick a valid start date and time.");
      return;
    }
    if (parsedStart.getTime() < Date.now()) {
      setFormError("The start time must be later today or in the future.");
      return;
    }

    setCreating(true);
    try {
      const created = await createWorkshop(title.trim(), parsedStart.toISOString(), capacityNumber);
      setWorkshops((items) => [...items, created]);
      setTitle("");
      setStartsAt("");
      setCapacity("10");
      setError(null);
      showToast("Workshop created and added to the calendar.", "success");
    } catch (requestError) {
      setError(requestError);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(workshopId: string) {
    setDeletingId(workshopId);
    try {
      await deleteWorkshop(workshopId);
      setWorkshops((items) => items.filter((workshop) => workshop.id !== workshopId));
      showToast("Workshop cancelled and removed from the calendar.", "success");
    } catch (requestError) {
      setError(requestError);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div id="account-view" className="min-h-screen px-5 py-6 sm:px-8 sm:py-10">
      <div className="mx-auto max-w-3xl">
        <button
          id="account-back-button"
          type="button"
          onClick={onClose}
          className="font-semibold text-teal hover:text-coral"
        >
          ← Back to workshops
        </button>
        <p className="mt-12 text-sm font-bold uppercase tracking-[.2em] text-coral">Your account</p>
        <h1 className="display-font mt-3 text-5xl font-bold tracking-[-.06em]">
          Your reservations
        </h1>
        <p className="mt-4 text-ink/70">Everything you have booked, wherever you sign in.</p>
        {user?.is_admin && (
          <section
            id="admin-workshop-manager"
            className="mt-10 rounded-3xl border border-teal/30 bg-teal-light/45 p-6 sm:p-8"
          >
            <p className="text-xs font-bold uppercase tracking-[.18em] text-teal">Admin tools</p>
            <h2 className="display-font mt-2 text-3xl font-bold">Manage sessions</h2>
            <form onSubmit={handleCreate} className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="text-sm font-semibold md:col-span-2">
                Workshop title
                <input
                  id="admin-workshop-title"
                  required
                  minLength={1}
                  maxLength={200}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                  placeholder="Designing reliable APIs"
                />
              </label>
              <label className="text-sm font-semibold">
                Start date and time
                <input
                  id="admin-workshop-starts-at"
                  required
                  type="datetime-local"
                  min={earliestStart}
                  value={startsAt}
                  onChange={(event) => setStartsAt(event.target.value)}
                  className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                />
                <span className="mt-1 block text-xs font-normal text-ink/70">
                  Interpreted in your local timezone. Must be later today or in the future.
                </span>
              </label>
              <label className="text-sm font-semibold">
                Capacity
                <input
                  id="admin-workshop-capacity"
                  required
                  min="1"
                  step="1"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  type="number"
                  value={capacity}
                  onChange={(event) => setCapacity(event.target.value.replace(/[^\d]/g, ""))}
                  className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                />
                <span className="mt-1 block text-xs font-normal text-ink/70">
                  Whole number of seats, 1 or more.
                </span>
              </label>
              <button
                id="admin-create-workshop-button"
                type="submit"
                disabled={creating}
                className="justify-self-start rounded-full bg-teal px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:opacity-60"
              >
                {creating ? "Creating..." : "Create workshop"}
              </button>
              {formError && (
                <p
                  id="admin-workshop-form-error"
                  className="md:col-span-2 rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
                  role="alert"
                >
                  {formError}
                </p>
              )}
            </form>
            <ul id="admin-workshop-list" className="mt-8 space-y-3">
              {workshops.map((workshop) => (
                <li
                  key={workshop.id}
                  className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-line bg-paper/90 p-4"
                >
                  <div>
                    <p className="font-semibold">{workshop.title}</p>
                    <p className="mt-1 text-xs text-ink/70">
                      {formatDate(workshop.starts_at)} · {workshop.available_spots} of{" "}
                      {workshop.max_capacity} seats open
                    </p>
                  </div>
                  <button
                    id={`admin-delete-workshop-${workshop.id}`}
                    type="button"
                    disabled={deletingId === workshop.id}
                    onClick={() => setPendingDeleteId(workshop.id)}
                    className="rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-coral hover:bg-soft disabled:opacity-50"
                  >
                    {deletingId === workshop.id ? "Cancelling..." : "Cancel session"}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {loading ? (
          <div id="account-loading" className="mt-10 space-y-3" aria-label="Loading reservations">
            <div className="h-28 animate-pulse rounded-3xl bg-sand" />
            <div className="h-28 animate-pulse rounded-3xl bg-sand" />
          </div>
        ) : reservations.length === 0 ? (
          <div
            id="account-empty-state"
            className="mt-10 rounded-3xl border border-dashed border-line bg-paper/60 px-6 py-14 text-center"
          >
            <p className="display-font text-xl font-semibold">No reservations yet.</p>
            <p className="mt-2 text-sm text-ink/70">
              Your next good idea is waiting in the calendar.
            </p>
            <button
              id="account-empty-browse-button"
              type="button"
              onClick={onClose}
              className="mt-6 inline-flex items-center justify-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-bold text-paper transition hover:bg-teal"
            >
              Browse the calendar <span aria-hidden="true">→</span>
            </button>
          </div>
        ) : (
          <ul id="account-reservations-list" className="mt-10 space-y-4">
            {reservations.map((reservation) => (
              <li
                key={reservation.id}
                id={`account-reservation-${reservation.id}`}
                className="rounded-3xl border border-line bg-paper/80 p-6"
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-bold uppercase tracking-wider text-teal">
                      {reservation.status === "active" ? "Confirmed" : "Cancelled"}
                    </p>
                    <h2 className="display-font mt-2 text-2xl font-bold">
                      {reservation.workshop_title}
                    </h2>
                    <p className="mt-2 text-sm text-ink/75">
                      Booked {formatDate(reservation.created_at)}
                    </p>
                  </div>
                  {reservation.status === "active" && (
                    <button
                      id={`account-cancel-button-${reservation.id}`}
                      type="button"
                      onClick={() => setPendingCancelId(reservation.id)}
                      className="rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-coral hover:bg-soft"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <ConfirmDialog
          open={pendingCancelId !== null}
          title="Cancel this reservation?"
          description="Your seat will be released and offered to the next person. You can re-reserve anytime if seats are still open."
          confirmLabel="Yes, cancel my seat"
          onConfirm={async () => {
            if (pendingCancelId) {
              await cancel(pendingCancelId);
              setPendingCancelId(null);
            }
          }}
          onCancel={() => setPendingCancelId(null)}
        />
        <ConfirmDialog
          open={pendingDeleteId !== null}
          title="Cancel this session?"
          description="This removes the workshop from the calendar. If anyone is already booked they will be told the session is no longer running."
          confirmLabel="Yes, cancel the session"
          busy={pendingDeleteId !== null && deletingId === pendingDeleteId}
          onConfirm={async () => {
            if (pendingDeleteId) {
              await handleDelete(pendingDeleteId);
              setPendingDeleteId(null);
            }
          }}
          onCancel={() => setPendingDeleteId(null)}
        />
      </div>
    </div>
  );
}
