/**
 * Admin view: the session-management surface.
 *
 * Data flow:
 * - The session list comes from `listWorkshops({ state:
 *   "all", limit: 100 })` so past and upcoming sessions
 *   are both manageable. List items omit
 *   description/category/location, so opening the edit
 *   form fetches the detail endpoint for the full field
 *   set.
 * - The summary cards and the per-workshop attendee
 *   lists come from `getOrganizerStats()`
 *   (`GET /api/organizer/stats`), which aggregates over
 *   every workshop the caller may manage.
 * - Mutations (create / edit / publish / cancel / delete)
 *   update the local list from the mutation response so
 *   the surface feels instant; the stats payload is
 *   re-fetched afterwards because the booking counts
 *   change.
 *
 * `ends_at` and `registration_closes_at` are optional:
 * each has a checkbox affordance, and `null` is sent
 * when the option is unchecked. `datetime-local` inputs
 * are converted to ISO-8601 in the user's local
 * timezone on submit and back on prefill.
 *
 * Destructive steps (cancel / delete) go through
 * `ConfirmDialog`. API failures are normalized into
 * `ApiError` and surfaced through the global toast
 * layer; client-side validation renders inline in the
 * form banner.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@ws/api-client";
import type { OrganizerDashboardResponse, Workshop } from "@ws/types";

import { getApiClient } from "../apiClient";
import { useToast, useToastError } from "@ws/ui";
import { ConfirmDialog } from "@ws/ui";
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
 * Convert an ISO-8601 timestamp to a `datetime-local`
 * input value in the user's local timezone.
 *
 * @param isoValue - The timezone-aware timestamp.
 * @returns The `datetime-local` input value.
 */
function toLocalInput(isoValue: string): string {
  const date = new Date(isoValue);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
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
 * Render the admin session manager.
 *
 * Fetches the session list and the organizer stats on
 * mount, exposes the full create/edit form and the
 * publish / cancel / delete lifecycle controls, and
 * surfaces API errors through the global toast layer.
 */
export function AdminView({ onClose }: Props) {
  const { showToast } = useToast();
  const [workshops, setWorkshops] = useState<Workshop[]>([]);
  const [stats, setStats] = useState<OrganizerDashboardResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [listLoading, setListLoading] = useState(true);
  const [expandedIds, setExpandedIds] = useState<string[]>([]);

  // Form state. `editingId` is null while creating.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [hasEndsAt, setHasEndsAt] = useState(false);
  const [registrationClosesAt, setRegistrationClosesAt] = useState("");
  const [hasRegistrationClosesAt, setHasRegistrationClosesAt] = useState(false);
  const [capacity, setCapacity] = useState("10");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Lifecycle action state.
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // Guards the detail prefill: a slow response for a
  // previously opened session must not overwrite the
  // form of the session currently being edited.
  const detailRequestId = useRef<string | null>(null);

  useToastError(error, () => setError(null));

  const reload = useCallback(() => {
    setListLoading(true);
    // The admin manager lists every session regardless of
    // timing, so it asks for the "all" state and the
    // largest page the endpoint allows.
    getApiClient()
      .workshops.listWorkshops({ state: "all", limit: 100 })
      .then((envelope) => setWorkshops(envelope.items))
      .catch(setError)
      .finally(() => setListLoading(false));
  }, []);

  const loadStats = useCallback(() => {
    getApiClient()
      .workshops.getOrganizerStats()
      .then(setStats)
      .catch(setError);
  }, []);

  useEffect(() => {
    reload();
    loadStats();
  }, [reload, loadStats]);

  /** Reset the form back to create mode. */
  function resetForm() {
    setEditingId(null);
    setTitle("");
    setStartsAt("");
    setEndsAt("");
    setHasEndsAt(false);
    setRegistrationClosesAt("");
    setHasRegistrationClosesAt(false);
    setCapacity("10");
    setDescription("");
    setCategory("");
    setLocation("");
    setFormError(null);
  }

  /**
   * Open the form prefilled for editing. The list envelope
   * omits description/category/location, so the detail
   * endpoint is fetched for the full field set.
   *
   * @param workshop - The session to edit.
   */
  function openEdit(workshop: Workshop) {
    setFormError(null);
    setEditingId(workshop.id);
    setTitle(workshop.title);
    setStartsAt(toLocalInput(workshop.starts_at));
    setHasEndsAt(workshop.ends_at != null);
    setEndsAt(workshop.ends_at != null ? toLocalInput(workshop.ends_at) : "");
    setHasRegistrationClosesAt(workshop.registration_closes_at != null);
    setRegistrationClosesAt(
      workshop.registration_closes_at != null ? toLocalInput(workshop.registration_closes_at) : "",
    );
    setCapacity(String(workshop.max_capacity));
    setDescription("");
    setCategory("");
    setLocation("");
    detailRequestId.current = workshop.id;
    getApiClient()
      .workshops.getWorkshop(workshop.id)
      .then((detail) => {
        if (detailRequestId.current !== workshop.id) return;
        setDescription(detail.description ?? "");
        setCategory(detail.category ?? "");
        setLocation(detail.location ?? "");
      })
      .catch(setError);
  }

  /** Show or hide one workshop's attendee list. */
  function toggleAttendees(workshopId: string) {
    setExpandedIds((current) =>
      current.includes(workshopId)
        ? current.filter((id) => id !== workshopId)
        : [...current, workshopId],
    );
  }

  /** Look up the organizer-stats row for a workshop. */
  function statsFor(workshopId: string) {
    return stats?.workshops?.find((entry) => entry.workshop_id === workshopId) ?? null;
  }

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

    // Start time: must be a parseable timestamp. A new
    // session must start now or in the future; an existing
    // session may legitimately sit in the past.
    const parsedStart = new Date(startsAt);
    if (Number.isNaN(parsedStart.getTime())) {
      setFormError("Pick a valid start date and time.");
      return;
    }
    if (!editingId && parsedStart.getTime() < Date.now()) {
      setFormError("The start time must be later today or in the future.");
      return;
    }

    // End time: optional, but when set it must follow the start.
    let parsedEnd: Date | null = null;
    if (hasEndsAt) {
      parsedEnd = new Date(endsAt);
      if (Number.isNaN(parsedEnd.getTime())) {
        setFormError("Pick a valid end date and time, or clear the end time option.");
        return;
      }
      if (parsedEnd.getTime() <= parsedStart.getTime()) {
        setFormError("The end time must be after the start time.");
        return;
      }
    }

    // Registration deadline: optional, but when set it must
    // be in the future and no later than the start time
    // (the backend rejects both with a 422).
    let parsedCloses: Date | null = null;
    if (hasRegistrationClosesAt) {
      parsedCloses = new Date(registrationClosesAt);
      if (Number.isNaN(parsedCloses.getTime())) {
        setFormError("Pick a valid registration deadline, or clear the deadline option.");
        return;
      }
      if (parsedCloses.getTime() < Date.now()) {
        setFormError("The registration deadline must be in the future.");
        return;
      }
      if (parsedCloses.getTime() > parsedStart.getTime()) {
        setFormError("The registration deadline must be before the start time.");
        return;
      }
    }

    // Unset optional fields are sent as explicit nulls so
    // the server clears them instead of keeping the old value.
    const payload = {
      title: title.trim(),
      starts_at: toIso(startsAt),
      ends_at: parsedEnd !== null ? toIso(endsAt) : null,
      max_capacity: capacityNumber,
      description: description.trim(),
      category: category.trim(),
      location: location.trim(),
      registration_closes_at: parsedCloses !== null ? toIso(registrationClosesAt) : null,
    };

    setSubmitting(true);
    try {
      if (editingId) {
        const updated = await getApiClient().workshops.updateWorkshop(editingId, payload);
        setWorkshops((items) => items.map((item) => (item.id === editingId ? updated : item)));
        showToast("Session updated.", "success");
      } else {
        const created = await getApiClient().workshops.createWorkshop(payload);
        setWorkshops((items) => [...items, created]);
        showToast("Workshop created and added to the calendar.", "success");
      }
      resetForm();
      loadStats();
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.code === "workshop_not_found") {
        // The session was removed elsewhere; drop the stale row.
        reload();
      }
      setError(requestError);
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePublish(workshopId: string) {
    setPublishingId(workshopId);
    try {
      const updated = await getApiClient().workshops.publishWorkshop(workshopId);
      setWorkshops((items) => items.map((item) => (item.id === workshopId ? updated : item)));
      showToast("Session published and now bookable.", "success");
      loadStats();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setPublishingId(null);
    }
  }

  async function handleCancel(workshopId: string) {
    setCancellingId(workshopId);
    try {
      const updated = await getApiClient().workshops.cancelWorkshop(workshopId);
      setWorkshops((items) => items.map((item) => (item.id === workshopId ? updated : item)));
      showToast("Session cancelled.", "success");
      loadStats();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setCancellingId(null);
    }
  }

  async function handleDelete(workshopId: string) {
    setDeletingId(workshopId);
    try {
      await getApiClient().workshops.deleteWorkshop(workshopId);
      setWorkshops((items) => items.filter((item) => item.id !== workshopId));
      showToast("Workshop deleted from the calendar.", "success");
      loadStats();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div id="admin-view" className="min-h-screen px-5 py-6 sm:px-8 sm:py-10">
      <div className="mx-auto max-w-4xl">
        <button
          id="admin-back-button"
          type="button"
          onClick={onClose}
          className="font-semibold text-teal hover:text-coral"
        >
          ← Back to workshops
        </button>
        <p className="mt-12 text-sm font-bold uppercase tracking-[.2em] text-coral">Admin tools</p>
        <h1 className="display-font mt-3 text-5xl font-bold tracking-[-.06em]">Manage sessions</h1>
        <p className="mt-4 text-ink/70">
          Create, edit, publish, and cancel sessions — and see exactly who booked each one.
        </p>

        <section
          id="admin-stats"
          className="mt-10 grid gap-4 sm:grid-cols-2"
          aria-label="Booking summary"
        >
          <div
            id="admin-stats-total-bookings-card"
            className="rounded-3xl border border-line bg-paper/80 p-6"
          >
            <p className="text-xs font-bold uppercase tracking-wider text-ink/60">Total bookings</p>
            <p
              id="admin-stats-total-bookings"
              className="display-font mt-2 text-4xl font-bold text-teal"
            >
              {stats ? String(stats.total_bookings) : "—"}
            </p>
            <p className="mt-1 text-sm text-ink/70">Active reservations across your sessions.</p>
          </div>
          <div
            id="admin-stats-upcoming-sessions-card"
            className="rounded-3xl border border-line bg-paper/80 p-6"
          >
            <p className="text-xs font-bold uppercase tracking-wider text-ink/60">
              Upcoming sessions
            </p>
            <p
              id="admin-stats-upcoming-sessions"
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
          id="admin-workshop-manager"
          className="mt-10 rounded-3xl border border-teal/30 bg-teal-light/45 p-6 sm:p-8"
        >
          <p className="text-xs font-bold uppercase tracking-[.18em] text-teal">Session details</p>
          <h2 className="display-font mt-2 text-3xl font-bold">
            {editingId ? "Edit session" : "New session"}
          </h2>
          <form onSubmit={handleSubmit} className="mt-6 grid gap-4 md:grid-cols-2">
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
                min={editingId ? undefined : localNow()}
                value={startsAt}
                onChange={(event) => setStartsAt(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
              />
              <span className="mt-1 block text-xs font-normal text-ink/70">
                Interpreted in your local timezone.
                {editingId ? "" : " Must be later today or in the future."}
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
            <div>
              <div className="flex items-center justify-between gap-3">
                <label htmlFor="admin-workshop-ends-at" className="text-sm font-semibold">
                  End date and time
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-ink/70">
                  <input
                    id="admin-workshop-ends-at-optional"
                    type="checkbox"
                    checked={hasEndsAt}
                    onChange={(event) => setHasEndsAt(event.target.checked)}
                    className="h-4 w-4 accent-teal"
                  />
                  Set an end time
                </label>
              </div>
              <input
                id="admin-workshop-ends-at"
                type="datetime-local"
                disabled={!hasEndsAt}
                value={endsAt}
                onChange={(event) => setEndsAt(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3 disabled:opacity-50"
              />
              <span className="mt-1 block text-xs font-normal text-ink/70">
                Optional — leave unchecked when the session has no fixed end.
              </span>
            </div>
            <div>
              <div className="flex items-center justify-between gap-3">
                <label
                  htmlFor="admin-workshop-registration-closes-at"
                  className="text-sm font-semibold"
                >
                  Registration deadline
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-ink/70">
                  <input
                    id="admin-workshop-registration-closes-at-optional"
                    type="checkbox"
                    checked={hasRegistrationClosesAt}
                    onChange={(event) => setHasRegistrationClosesAt(event.target.checked)}
                    className="h-4 w-4 accent-teal"
                  />
                  Set a deadline
                </label>
              </div>
              <input
                id="admin-workshop-registration-closes-at"
                type="datetime-local"
                disabled={!hasRegistrationClosesAt}
                value={registrationClosesAt}
                onChange={(event) => setRegistrationClosesAt(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3 disabled:opacity-50"
              />
              <span className="mt-1 block text-xs font-normal text-ink/70">
                Optional — booking closes when the session starts unless set.
              </span>
            </div>
            <label className="text-sm font-semibold">
              Category
              <input
                id="admin-workshop-category"
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
                id="admin-workshop-location"
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
                id="admin-workshop-description"
                rows={4}
                maxLength={4000}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="What attendees will walk away with…"
              />
            </label>
            <div className="flex flex-wrap items-center gap-3 md:col-span-2">
              <button
                id="admin-create-workshop-button"
                type="submit"
                disabled={submitting}
                className="rounded-full bg-teal px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:opacity-60"
              >
                {submitting ? "Saving..." : editingId ? "Save changes" : "Create workshop"}
              </button>
              {editingId && (
                <button
                  id="admin-cancel-edit-button"
                  type="button"
                  onClick={resetForm}
                  disabled={submitting}
                  className="rounded-full border border-line px-5 py-3 text-sm font-bold text-ink/75 hover:border-teal hover:text-teal disabled:opacity-60"
                >
                  Cancel editing
                </button>
              )}
            </div>
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
        </section>

        <section className="mt-10">
          <p className="text-xs font-bold uppercase tracking-[.18em] text-teal">Calendar</p>
          <h2 className="display-font mt-2 text-3xl font-bold">All sessions</h2>
          <p className="mt-2 text-sm text-ink/70">
            Every session on the calendar, with its lifecycle controls and attendee list.
          </p>
          {listLoading ? (
            <div
              id="admin-workshop-loading"
              className="mt-6 space-y-3"
              aria-label="Loading sessions"
            >
              <div className="h-28 animate-pulse rounded-3xl bg-sand" />
              <div className="h-28 animate-pulse rounded-3xl bg-sand" />
            </div>
          ) : workshops.length === 0 ? (
            <div
              id="admin-workshop-empty"
              className="mt-6 rounded-3xl border border-dashed border-line bg-paper/60 px-6 py-14 text-center"
            >
              <p className="display-font text-xl font-semibold">No sessions yet.</p>
              <p className="mt-2 text-sm text-ink/70">Create the first one with the form above.</p>
            </div>
          ) : (
            <ul id="admin-workshop-list" className="mt-6 space-y-3">
              {workshops.map((workshop) => {
                const workshopStats = statsFor(workshop.id);
                const attendeeCount = workshopStats?.attendees?.length ?? 0;
                const expanded = expandedIds.includes(workshop.id);
                const statusBadge =
                  workshop.status === "draft"
                    ? "rounded-full bg-sand px-3 py-1 text-xs font-bold text-ink/70"
                    : workshop.status === "cancelled"
                      ? "rounded-full bg-soft px-3 py-1 text-xs font-bold text-danger"
                      : "rounded-full bg-teal-light px-3 py-1 text-xs font-bold text-teal";
                return (
                  <li
                    key={workshop.id}
                    id={`admin-workshop-item-${workshop.id}`}
                    className="rounded-2xl border border-line bg-paper/90 p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-semibold">{workshop.title}</p>
                          <span id={`admin-workshop-status-${workshop.id}`} className={statusBadge}>
                            {workshop.status}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-ink/70">
                          {formatDate(workshop.starts_at)} · {formatTime(workshop.starts_at)} ·{" "}
                          {workshop.available_spots} of {workshop.max_capacity} seats open
                        </p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          id={`admin-edit-workshop-${workshop.id}`}
                          type="button"
                          onClick={() => openEdit(workshop)}
                          className="rounded-full border border-line px-4 py-2 text-xs font-bold text-teal hover:border-teal"
                        >
                          Edit
                        </button>
                        {workshop.status === "draft" && (
                          <button
                            id={`admin-publish-workshop-${workshop.id}`}
                            type="button"
                            disabled={publishingId === workshop.id}
                            onClick={() => void handlePublish(workshop.id)}
                            className="rounded-full bg-teal px-4 py-2 text-xs font-bold text-white hover:bg-ink disabled:opacity-60"
                          >
                            {publishingId === workshop.id ? "Publishing..." : "Publish"}
                          </button>
                        )}
                        {workshop.status !== "cancelled" && (
                          <button
                            id={`admin-cancel-workshop-${workshop.id}`}
                            type="button"
                            onClick={() => setPendingCancelId(workshop.id)}
                            className="rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-coral hover:bg-soft"
                          >
                            Cancel
                          </button>
                        )}
                        <button
                          id={`admin-delete-workshop-${workshop.id}`}
                          type="button"
                          onClick={() => setPendingDeleteId(workshop.id)}
                          className="rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-coral hover:bg-soft"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                    <button
                      id={`admin-workshop-attendees-toggle-${workshop.id}`}
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => toggleAttendees(workshop.id)}
                      className="mt-4 flex w-full items-center justify-between rounded-xl border border-line bg-paper/70 px-4 py-2.5 text-left text-xs font-bold text-ink/75 hover:border-teal/60 hover:text-teal"
                    >
                      <span>Attendees</span>
                      <span aria-hidden="true">
                        {expanded ? "▾" : "▸"} {attendeeCount}
                      </span>
                    </button>
                    {expanded && (
                      <ul
                        id={`admin-workshop-attendees-list-${workshop.id}`}
                        className="mt-2 space-y-2"
                        aria-label={`Attendees for ${workshop.title}`}
                      >
                        {workshopStats === null || (workshopStats.attendees ?? []).length === 0 ? (
                          <li className="rounded-xl border border-dashed border-line px-4 py-3 text-xs text-ink/70">
                            No attendees yet.
                          </li>
                        ) : (
                          (workshopStats.attendees ?? []).map((attendee) => (
                            <li
                              key={attendee.reservation_id}
                              id={`admin-workshop-attendee-${attendee.reservation_id}`}
                              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-paper/70 px-4 py-3"
                            >
                              <div className="min-w-0">
                                <p className="text-sm font-semibold">{attendee.attendee_name}</p>
                                <p className="truncate text-xs text-ink/60">
                                  {attendee.attendee_email}
                                </p>
                              </div>
                              <div className="flex items-center gap-2">
                                <span
                                  id={`admin-attendee-code-${attendee.reservation_id}`}
                                  className="rounded-full bg-sand px-3 py-1 font-mono text-xs font-bold text-ink"
                                >
                                  {attendee.booking_code}
                                </span>
                                <span
                                  id={`admin-attendee-status-${attendee.reservation_id}`}
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

        <ConfirmDialog
          open={pendingCancelId !== null}
          title="Cancel this session?"
          description="The session is removed from the calendar. This is refused while attendees are still booked."
          confirmLabel="Yes, cancel the session"
          busy={pendingCancelId !== null && cancellingId === pendingCancelId}
          onConfirm={async () => {
            if (pendingCancelId) {
              await handleCancel(pendingCancelId);
              setPendingCancelId(null);
            }
          }}
          onCancel={() => setPendingCancelId(null)}
        />
        <ConfirmDialog
          open={pendingDeleteId !== null}
          title="Delete this session?"
          description="The session is permanently removed from the calendar. This is refused while attendees are still booked."
          confirmLabel="Yes, delete the session"
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
