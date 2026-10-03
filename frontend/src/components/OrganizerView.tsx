/**
 * Organizer dashboard: the organizer-platform home.
 *
 * Data flow:
 * - The summary cards and the per-workshop breakdown
 *   come from `getOrganizerStats()`
 *   (`GET /api/organizer/stats`), which aggregates over
 *   every workshop the caller may manage: every workshop
 *   for the super-admin, or the workshops owned by the
 *   caller's organizations. The payload carries the
 *   total active bookings, the count of upcoming
 *   published sessions, and a per-workshop breakdown
 *   with booking counts and the full attendee list
 *   (including booking codes).
 * - The quick-create form publishes a platform-managed
 *   session (`POST /api/workshops` without an
 *   `organization_id`); the stats payload does not
 *   carry organization ids, so an organization picker
 *   is out of scope.
 *
 * `starts_at` is a `datetime-local` input converted to
 * ISO-8601 in the user's local timezone on submit.
 *
 * API failures are normalized into `ApiError` and
 * surfaced through the global toast layer; client-side
 * validation renders inline in the form banner.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, createWorkshop, getOrganizerStats } from "../api";
import type { OrganizerDashboardResponse } from "../types";
import { useToast, useToastError } from "./Toast";
import { formatDate, formatTime } from "../utils/formatters";

interface Props {
  /** Called when the user clicks "Back to workshops". */
  onClose: () => void;
}

/**
 * Convert a `datetime-local` input value to an ISO-8601
 * timestamp in the user's local timezone.
 *
 * @param localValue - The `datetime-local` input value.
 * @returns The timezone-aware ISO-8601 timestamp.
 */
function toIso(localValue: string): string {
  return new Date(localValue).toISOString();
}

/**
 * Format "now" as a `datetime-local` value in the user's
 * local timezone (no offset suffix) — the earliest time
 * a new session can start.
 *
 * @returns The `datetime-local` value for the current moment.
 */
function localNow(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}`
  );
}

/**
 * Render the organizer dashboard.
 *
 * Fetches the organizer stats on mount, exposes a
 * quick-create session form, and renders the per-workshop
 * breakdown with expandable attendee lists. API errors
 * surface through the global toast layer.
 */
export function OrganizerView({ onClose }: Props) {
  const { showToast } = useToast();
  const [stats, setStats] = useState<OrganizerDashboardResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [expandedIds, setExpandedIds] = useState<string[]>([]);

  // Quick-create form state.
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [capacity, setCapacity] = useState("10");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useToastError(error, () => setError(null));

  const loadStats = useCallback(() => {
    setLoading(true);
    getOrganizerStats()
      .then(setStats)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  /** Show or hide one workshop's attendee list. */
  function toggleAttendees(workshopId: string) {
    setExpandedIds((current) =>
      current.includes(workshopId)
        ? current.filter((id) => id !== workshopId)
        : [...current, workshopId],
    );
  }

  /**
   * Publish a platform-managed session from the quick-create
   * form. The created session is not attached to an
   * organization; the stats are re-fetched afterwards
   * because the counts change.
   */
  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    // Capacity: strip anything that is not a digit, then
    // parse. The input is type=number but the browser will
    // still let a paste bring in signs or exponent markers
    // on some platforms; the value is sanitised here so the
    // only thing the server sees is an integer >= 1.
    const capacityDigits = capacity.replace(/\D+/g, "");
    const capacityNumber = Number.parseInt(capacityDigits, 10);
    if (!Number.isFinite(capacityNumber) || capacityNumber < 1) {
      setFormError("Capacity must be a positive whole number.");
      return;
    }

    // Start time: must be a parseable timestamp in the
    // future (the backend rejects a past start with a 422).
    const parsedStart = new Date(startsAt);
    if (Number.isNaN(parsedStart.getTime())) {
      setFormError("Pick a valid start date and time.");
      return;
    }
    if (parsedStart.getTime() < Date.now()) {
      setFormError("The start time must be later today or in the future.");
      return;
    }

    setSubmitting(true);
    try {
      await createWorkshop({
        title: title.trim(),
        starts_at: toIso(startsAt),
        max_capacity: capacityNumber,
        description: description.trim(),
        category: category.trim(),
        location: location.trim(),
      });
      setTitle("");
      setStartsAt("");
      setCapacity("10");
      setDescription("");
      setCategory("");
      setLocation("");
      showToast("Session created and added to the calendar.", "success");
      loadStats();
    } catch (requestError) {
      if (requestError instanceof ApiError) {
        setFormError(requestError.message);
      } else {
        setFormError("Failed to create the session.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div id="organizer-view" className="min-h-screen px-5 py-6 sm:px-8 sm:py-10">
      <div className="mx-auto max-w-4xl">
        <button
          id="organizer-back-button"
          type="button"
          onClick={onClose}
          className="font-semibold text-teal hover:text-coral"
        >
          ← Back to workshops
        </button>
        <p className="mt-12 text-sm font-bold uppercase tracking-[.2em] text-coral">
          Organizer platform
        </p>
        <h1 className="display-font mt-3 text-5xl font-bold tracking-[-.06em]">
          Your dashboard
        </h1>
        <p className="mt-4 text-ink/70">
          Booking totals, attendee lists, and a quick way to add a session.
        </p>

        <section
          id="organizer-stats"
          className="mt-10 grid gap-4 sm:grid-cols-2"
          aria-label="Booking summary"
        >
          <div
            id="organizer-stats-total-bookings-card"
            className="rounded-3xl border border-line bg-paper/80 p-6"
          >
            <p className="text-xs font-bold uppercase tracking-wider text-ink/60">
              Total bookings
            </p>
            <p
              id="organizer-stats-total-bookings"
              className="display-font mt-2 text-4xl font-bold text-teal"
            >
              {stats ? String(stats.total_bookings) : "—"}
            </p>
            <p className="mt-1 text-sm text-ink/70">
              Active reservations across your sessions.
            </p>
          </div>
          <div
            id="organizer-stats-upcoming-sessions-card"
            className="rounded-3xl border border-line bg-paper/80 p-6"
          >
            <p className="text-xs font-bold uppercase tracking-wider text-ink/60">
              Upcoming sessions
            </p>
            <p
              id="organizer-stats-upcoming-sessions"
              className="display-font mt-2 text-4xl font-bold text-teal"
            >
              {stats ? String(stats.upcoming_sessions) : "—"}
            </p>
            <p className="mt-1 text-sm text-ink/70">
              Published sessions that have not started yet.
            </p>
          </div>
        </section>

        <section
          id="organizer-quick-create"
          className="mt-10 rounded-3xl border border-teal/30 bg-teal-light/45 p-6 sm:p-8"
        >
          <p className="text-xs font-bold uppercase tracking-[.18em] text-teal">
            Quick create
          </p>
          <h2 className="display-font mt-2 text-3xl font-bold">New session</h2>
          <p className="mt-2 text-sm text-ink/70">
            A platform-managed session, bookable from the calendar immediately.
          </p>
          <form onSubmit={handleSubmit} className="mt-6 grid gap-4 md:grid-cols-2">
            <label className="text-sm font-semibold md:col-span-2">
              Workshop title
              <input
                id="organizer-workshop-title"
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
                id="organizer-workshop-starts-at"
                required
                type="datetime-local"
                min={localNow()}
                value={startsAt}
                onChange={(event) => setStartsAt(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
              />
              <span className="mt-1 block text-xs font-normal text-ink/70">
                Interpreted in your local timezone. Must be later today or in the
                future.
              </span>
            </label>
            <label className="text-sm font-semibold">
              Capacity
              <input
                id="organizer-workshop-capacity"
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
            <label className="text-sm font-semibold">
              Category
              <input
                id="organizer-workshop-category"
                maxLength={100}
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="Engineering"
              />
            </label>
            <label className="text-sm font-semibold">
              Location
              <input
                id="organizer-workshop-location"
                maxLength={200}
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="Room 4B"
              />
            </label>
            <label className="text-sm font-semibold md:col-span-2">
              Description
              <textarea
                id="organizer-workshop-description"
                rows={4}
                maxLength={4000}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="What attendees will walk away with…"
              />
            </label>
            <div className="md:col-span-2">
              <button
                id="organizer-create-workshop-button"
                type="submit"
                disabled={submitting}
                className="rounded-full bg-teal px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:opacity-60"
              >
                {submitting ? "Creating..." : "Create session"}
              </button>
            </div>
            {formError && (
              <p
                id="organizer-workshop-form-error"
                className="md:col-span-2 rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
                role="alert"
              >
                {formError}
              </p>
            )}
          </form>
        </section>

        <section className="mt-10">
          <p className="text-xs font-bold uppercase tracking-[.18em] text-teal">
            Sessions
          </p>
          <h2 className="display-font mt-2 text-3xl font-bold">
            Your workshops
          </h2>
          <p className="mt-2 text-sm text-ink/70">
            Every session you manage, with its booking count and attendee list.
          </p>
          {loading ? (
            <div
              id="organizer-workshop-loading"
              className="mt-6 space-y-3"
              aria-label="Loading your workshops"
            >
              <div className="h-28 animate-pulse rounded-3xl bg-sand" />
              <div className="h-28 animate-pulse rounded-3xl bg-sand" />
            </div>
          ) : !stats || stats.workshops.length === 0 ? (
            <div
              id="organizer-workshop-empty"
              className="mt-6 rounded-3xl border border-dashed border-line bg-paper/60 px-6 py-14 text-center"
            >
              <p className="display-font text-xl font-semibold">No sessions yet.</p>
              <p className="mt-2 text-sm text-ink/70">Create the first one with the form above.</p>
            </div>
          ) : (
            <ul id="organizer-workshop-list" className="mt-6 space-y-3">
              {stats.workshops.map((workshop) => {
                const expanded = expandedIds.includes(workshop.workshop_id);
                const statusBadge =
                  workshop.status === "draft"
                    ? "rounded-full bg-sand px-3 py-1 text-xs font-bold text-ink/70"
                    : workshop.status === "cancelled"
                      ? "rounded-full bg-soft px-3 py-1 text-xs font-bold text-danger"
                      : "rounded-full bg-teal-light px-3 py-1 text-xs font-bold text-teal";
                return (
                  <li
                    key={workshop.workshop_id}
                    id={`organizer-workshop-item-${workshop.workshop_id}`}
                    className="rounded-2xl border border-line bg-paper/90 p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-semibold">{workshop.title}</p>
                          <span
                            id={`organizer-workshop-status-${workshop.workshop_id}`}
                            className={statusBadge}
                          >
                            {workshop.status}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-ink/70">
                          {formatDate(workshop.starts_at)} · {formatTime(workshop.starts_at)}
                        </p>
                      </div>
                      <span
                        id={`organizer-workshop-booking-count-${workshop.workshop_id}`}
                        className="rounded-full bg-sand px-4 py-2 text-xs font-bold text-ink"
                      >
                        {workshop.booking_count}{" "}
                        {workshop.booking_count === 1 ? "booking" : "bookings"}
                      </span>
                    </div>
                    <button
                      id={`organizer-workshop-attendees-toggle-${workshop.workshop_id}`}
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => toggleAttendees(workshop.workshop_id)}
                      className="mt-4 flex w-full items-center justify-between rounded-xl border border-line bg-paper/70 px-4 py-2.5 text-left text-xs font-bold text-ink/75 hover:border-teal/60 hover:text-teal"
                    >
                      <span>Attendees</span>
                      <span aria-hidden="true">
                        {expanded ? "▾" : "▸"} {workshop.attendees.length}
                      </span>
                    </button>
                    {expanded && (
                      <ul
                        id={`organizer-workshop-attendees-list-${workshop.workshop_id}`}
                        className="mt-2 space-y-2"
                        aria-label={`Attendees for ${workshop.title}`}
                      >
                        {workshop.attendees.length === 0 ? (
                          <li className="rounded-xl border border-dashed border-line px-4 py-3 text-xs text-ink/70">
                            No attendees yet.
                          </li>
                        ) : (
                          workshop.attendees.map((attendee) => (
                            <li
                              key={attendee.reservation_id}
                              id={`organizer-workshop-attendee-${attendee.reservation_id}`}
                              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-paper/70 px-4 py-3"
                            >
                              <div className="min-w-0">
                                <p className="text-sm font-semibold">
                                  {attendee.attendee_name}
                                </p>
                                <p className="truncate text-xs text-ink/60">
                                  {attendee.attendee_email}
                                </p>
                              </div>
                              <div className="flex items-center gap-2">
                                <span
                                  id={`organizer-attendee-code-${attendee.reservation_id}`}
                                  className="rounded-full bg-sand px-3 py-1 font-mono text-xs font-bold text-ink"
                                >
                                  {attendee.booking_code}
                                </span>
                                <span
                                  id={`organizer-attendee-status-${attendee.reservation_id}`}
                                  className={
                                    attendee.status === "active"
                                      ? "rounded-full bg-teal-light px-3 py-1 text-xs font-bold text-teal"
                                      : "rounded-full bg-soft px-3 py-1 text-xs font-bold text-danger"
                                  }
                                >
                                  {attendee.status}
                                </span>
                              </div>
                            </li>
                          ))
                        )}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
