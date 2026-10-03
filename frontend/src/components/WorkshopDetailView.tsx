/**
 * Workshop detail view: hero header, seat-availability card,
 * reservation form, waitlist controls, and (for signed-in
 * users) the list of their own active reservations on this
 * workshop with cancel buttons.
 *
 * Data flow:
 * - `useWorkshopDetail` fetches the detail payload when its route,
 *   signed-in user, or relevant live event changes.
 * - When a relevant SSE event lands (the parent forwards the most
 *   recent event as a prop), refresh the detail so the reservation
 *   list and the waitlist position stay current. This covers the
 *   waitlist events (`waitlist_joined`, `waitlist_left`,
 *   `waitlist_promoted`, `waitlist_cancelled`) because every event
 *   for this workshop re-triggers the fetch.
 * - The displayed spot count prefers the live override passed in
 *   from the parent; falls back to the value from the detail
 *   payload.
 *
 * Waitlist: when the session is full the reserve card is replaced
 * by a "Join waitlist" action. The caller's queue position comes
 * from the detail payload (`waitlist_position`); the waitlist entry
 * id needed by the leave endpoint is not part of that payload, so
 * the id returned by the join endpoint is persisted per workshop
 * (see `saveWaitlistEntryId`) to keep "Leave waitlist" working
 * across reloads.
 *
 * Cancellation is local-state optimistic: the button is marked as
 * busy, the API is awaited, and the detail payload is then refreshed.
 * Failures surface in the global toast layer without losing the
 * user's scroll position.
 */
import { useEffect, useState } from "react";

import {
  ApiError,
  createReview,
  followOrganization,
  joinWaitlist,
  leaveWaitlist,
  listWorkshops,
  unfollowOrganization,
} from "../api";
import { useAuth } from "../features/auth/AuthContext";
import { useWorkshopDetail } from "../features/workshops/hooks/useWorkshopDetail";
import type { Reservation, SSEEvent } from "../types";
import { getToastErrorMessage, useToast, useToastError } from "./Toast";
import { formatDate, formatTime } from "../utils/formatters";
import { AuthPanel } from "./AuthPanel";
import { ConfirmationPanel } from "./ConfirmationPanel";
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
 * localStorage key prefix for waitlist entry ids.
 *
 * The workshop detail payload carries the caller's queue
 * position but not the entry id, while the leave-waitlist
 * endpoint requires that id. The id returned by the join
 * endpoint is therefore persisted per workshop so the
 * "Leave waitlist" action survives a reload.
 */
const WAITLIST_ENTRY_KEY = "waitlist-entry:";

/** Read the persisted waitlist entry id for a workshop, if any. */
function loadWaitlistEntryId(workshopId: string): string | null {
  try {
    return window.localStorage.getItem(WAITLIST_ENTRY_KEY + workshopId);
  } catch {
    return null;
  }
}

/** Persist (or clear) the waitlist entry id for a workshop. */
function saveWaitlistEntryId(workshopId: string, entryId: string | null): void {
  try {
    if (entryId === null) {
      window.localStorage.removeItem(WAITLIST_ENTRY_KEY + workshopId);
    } else {
      window.localStorage.setItem(WAITLIST_ENTRY_KEY + workshopId, entryId);
    }
  } catch {
    // Storage can be unavailable (private browsing); the
    // in-memory copy still covers the current session.
  }
}

/**
 * Map a failed waitlist request to user-facing copy.
 *
 * Branches on the stable machine `code` (never the message)
 * so the wording stays in sync with the backend contract.
 *
 * @param error - The caught failure.
 * @returns A human-readable message for the toast layer.
 */
function getWaitlistErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "registration_closed") {
      return "Registration for this session has closed.";
    }
    if (error.code === "already_reserved") {
      return "You already have a seat in this session.";
    }
    if (error.code === "workshop_full") {
      return "This session just filled up.";
    }
    if (error.code === "waitlist_entry_not_found") {
      return "That waitlist place no longer exists.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Failed to update the waitlist";
}

/**
 * Render the workshop detail view.
 *
 * The view has three visual states: loading (skeleton), error, and
 * loaded. The "fully booked" / "open" branch is driven entirely by
 * the spot count so the UI always agrees with the server. Lifecycle
 * badges ("Ended", "Registration closed") are derived from the
 * workshop's timestamps, and a closed registration window replaces
 * the reserve form with the deadline copy.
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
  const { showToast } = useToast();
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<Reservation | null>(null);
  const [waitlistEntryId, setWaitlistEntryId] = useState<string | null>(null);
  const [waitlistBusy, setWaitlistBusy] = useState(false);
  const [pendingLeaveWaitlist, setPendingLeaveWaitlist] = useState(false);
  const [leavingWaitlist, setLeavingWaitlist] = useState(false);

  // Follow state. `following` is null until the indirect
  // derivation (the "following" list filter) resolves;
  // `followersCount` is only known after a follow /
  // unfollow call because the detail payload does not
  // carry it.
  const [following, setFollowing] = useState<boolean | null>(null);
  const [followersCount, setFollowersCount] = useState<number | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [followMessage, setFollowMessage] = useState<string | null>(null);

  // Review form state.
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewText, setReviewText] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // Restore the persisted waitlist entry id when the workshop
  // changes so "Leave waitlist" works across reloads.
  useEffect(() => {
    setWaitlistEntryId(loadWaitlistEntryId(workshopId));
  }, [workshopId]);

  // The server is the source of truth for whether the caller
  // holds a place: when the position clears (promoted or
  // cancelled elsewhere), drop the persisted entry id too.
  useEffect(() => {
    if (detail !== null && detail.waitlist_position === null) {
      setWaitlistEntryId(null);
      saveWaitlistEntryId(workshopId, null);
    }
  }, [detail, workshopId]);

  // Derive whether the caller already follows this workshop's
  // organization. There is no GET /organizations/{id} endpoint,
  // so the only derivation available is the "following" list
  // filter: fetch the caller's followed-organization workshops
  // and check whether any item belongs to this organization.
  useEffect(() => {
    const organizationId = detail?.organization_id;
    if (!user || !organizationId) {
      setFollowing(null);
      setFollowersCount(null);
      return;
    }
    let active = true;
    listWorkshops({ following: true, limit: 100 })
      .then((envelope) => {
        if (!active) return;
        setFollowing(envelope.items.some((item) => item.organization_id === organizationId));
      })
      .catch(() => {
        // The derivation failed; assume "not following". Both
        // endpoints are idempotent, so a wrong guess self-
        // corrects on the first toggle.
        if (active) setFollowing(false);
      });
    return () => {
      active = false;
    };
  }, [user, detail?.organization_id]);

  /**
   * Toggle the follow state for this workshop's organization.
   *
   * The response carries the refreshed `followers_count`,
   * which becomes the displayed count. A member cannot
   * follow their own organization (409
   * `cannot_follow_own_organization`); that case renders
   * friendly inline copy instead of a toast.
   */
  async function handleFollowToggle() {
    const organizationId = detail?.organization_id;
    if (!user || !organizationId || following === null) return;
    setFollowBusy(true);
    setFollowMessage(null);
    try {
      if (following) {
        const updated = await unfollowOrganization(organizationId);
        setFollowersCount(updated.followers_count);
        setFollowing(false);
        showToast("You unfollowed this organization.", "info");
      } else {
        const updated = await followOrganization(organizationId);
        setFollowersCount(updated.followers_count);
        setFollowing(true);
        showToast("You're now following this organization.", "success");
      }
    } catch (requestError) {
      if (
        requestError instanceof ApiError &&
        requestError.code === "cannot_follow_own_organization"
      ) {
        setFollowMessage("You're a member of this organization.");
      } else {
        showToast(getToastErrorMessage(requestError), "error");
      }
    } finally {
      setFollowBusy(false);
    }
  }

  /**
   * Post a review for this workshop.
   *
   * The backend is authoritative on eligibility (a held
   * reservation and an ended session) and on the
   * one-review-per-user rule; both 409s render friendly
   * inline copy. On success the detail is refreshed so
   * the aggregate and the review list update.
   */
  async function handleSubmitReview(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setReviewError(null);
    setReviewSubmitting(true);
    try {
      await createReview(workshopId, reviewRating, reviewText.trim());
      setReviewText("");
      setReviewRating(5);
      await refresh();
      showToast("Review posted. Thanks for the feedback!", "success");
    } catch (requestError) {
      if (requestError instanceof ApiError) {
        if (requestError.code === "review_not_eligible") {
          setReviewError(
            "Reviews open after the session ends, for attendees who held a reservation.",
          );
          return;
        }
        if (requestError.code === "review_already_exists") {
          setReviewError("You've already reviewed this session.");
          return;
        }
      }
      showToast(getToastErrorMessage(requestError), "error");
    } finally {
      setReviewSubmitting(false);
    }
  }

  async function handleJoinWaitlist() {
    if (!user) {
      setAuthOpen(true);
      return;
    }
    setWaitlistBusy(true);
    try {
      const response = await joinWaitlist(workshopId);
      setWaitlistEntryId(response.entry.id);
      saveWaitlistEntryId(workshopId, response.entry.id);
      await refresh();
      showToast(
        response.replayed
          ? "You already hold a place in line."
          : `You're #${response.position} in line. A cancellation moves you up.`,
        "success",
      );
    } catch (requestError) {
      showToast(getWaitlistErrorMessage(requestError), "error");
    } finally {
      setWaitlistBusy(false);
    }
  }

  async function handleLeaveWaitlist() {
    const entryId = waitlistEntryId;
    if (!entryId) {
      setPendingLeaveWaitlist(false);
      showToast(
        "This place in line was joined on another device and can't be left from here.",
        "error",
      );
      return;
    }
    setLeavingWaitlist(true);
    try {
      await leaveWaitlist(entryId);
      setWaitlistEntryId(null);
      saveWaitlistEntryId(workshopId, null);
      await refresh();
      showToast("You left the waitlist.", "info");
    } catch (requestError) {
      showToast(getWaitlistErrorMessage(requestError), "error");
      if (requestError instanceof ApiError && requestError.code === "waitlist_entry_not_found") {
        // The entry was promoted or cancelled elsewhere; self-heal
        // the stale local copy so the badge agrees with the server.
        setWaitlistEntryId(null);
        saveWaitlistEntryId(workshopId, null);
        await refresh();
      }
    } finally {
      setLeavingWaitlist(false);
      setPendingLeaveWaitlist(false);
    }
  }

  function handleReserved(reservation: Reservation) {
    setConfirmation(reservation);
    void refresh();
  }

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
  const hasSeat = detail.reservations.length > 0;
  const now = new Date();
  const ended = detail.ends_at !== null && new Date(detail.ends_at) < now;
  // Review eligibility mirrors the backend gate: the
  // session has ended (`ends_at`, falling back to
  // `starts_at` when there is no explicit end) and
  // the caller holds a reservation on the workshop.
  // The backend remains authoritative — the 409s are
  // handled with friendly inline copy.
  const sessionEnded = new Date(detail.ends_at ?? detail.starts_at) < now;
  const hasReviewed = user !== null && detail.reviews.some((review) => review.user_id === user.id);
  const canReview = sessionEnded && hasSeat && !hasReviewed;
  const registrationDeadline = detail.registration_closes_at ?? detail.starts_at;
  const registrationClosed = new Date(registrationDeadline) < now;
  const statusLabel = ended
    ? "Ended"
    : registrationClosed
      ? "Registration closed"
      : full
        ? "Fully booked"
        : "Taking reservations";
  const availabilityClass =
    "rounded-full px-3 py-1.5 text-xs font-bold uppercase tracking-wider " +
    (ended
      ? "bg-sand text-ink/70"
      : registrationClosed || full
        ? "bg-coral text-white"
        : "bg-teal-light text-teal");
  // The waitlist status block is shared by the full card and
  // the reserve card so the position badge stays visible even
  // in the rare case of a waitlisted caller with an open seat
  // (e.g. after an admin capacity increase).
  const waitlistStatus =
    detail.waitlist_position !== null ? (
      <div id={`waitlist-status-${workshopId}`} className="mt-6">
        <span
          id={`waitlist-position-badge-${workshopId}`}
          className="inline-flex items-center gap-2 rounded-full bg-ink px-4 py-2 text-sm font-bold text-paper"
        >
          <span aria-hidden="true" className="text-teal-light">
            ◫
          </span>
          You&apos;re #{detail.waitlist_position} in line
        </span>
        <button
          id={`leave-waitlist-button-${workshopId}`}
          type="button"
          onClick={() => setPendingLeaveWaitlist(true)}
          className="mt-4 rounded-full border border-soft-edge px-4 py-2 text-xs font-bold text-danger hover:bg-soft"
        >
          Leave waitlist
        </button>
      </div>
    ) : null;
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
              {detail.ends_at && (
                <>
                  {" "}
                  <span className="text-ink/65">to</span> {formatDate(detail.ends_at)}
                </>
              )}
            </span>
            <span>
              <span className="text-ink/65">Starts at</span> {formatTime(detail.starts_at)}
              {detail.ends_at && (
                <>
                  {" "}
                  <span className="text-ink/65">ends at</span> {formatTime(detail.ends_at)}
                </>
              )}
            </span>
            {detail.location && (
              <span className="flex items-center gap-2">
                <span aria-hidden="true" className="text-teal">
                  ⌂
                </span>
                {detail.location}
              </span>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {detail.category && (
              <span
                id="workshop-detail-category"
                className="rounded-full bg-teal-light px-3 py-1 text-sm font-semibold text-teal"
              >
                {detail.category}
              </span>
            )}
            {!registrationClosed && !ended && detail.registration_closes_at && (
              <span
                id="workshop-detail-registration-deadline"
                className="rounded-full bg-sand px-3 py-1 text-sm font-semibold text-ink/75"
              >
                Registration closes {formatDate(detail.registration_closes_at)}
              </span>
            )}
            {detail.rating_average !== null && detail.rating_count > 0 && (
              <span
                id="workshop-detail-rating"
                className="rounded-full bg-sand px-3 py-1 text-sm font-semibold text-ink/75"
              >
                {detail.rating_average.toFixed(1)} · {detail.rating_count}{" "}
                {detail.rating_count === 1 ? "review" : "reviews"}
              </span>
            )}
          </div>
          {detail.organization_id && user && (
            <div
              id={`follow-controls-${workshopId}`}
              className="mt-4 flex flex-wrap items-center gap-3"
            >
              <button
                id={`follow-button-${workshopId}`}
                type="button"
                disabled={followBusy || following === null}
                onClick={() => void handleFollowToggle()}
                className={
                  following
                    ? "rounded-full bg-teal px-4 py-2 text-xs font-bold text-white hover:bg-ink disabled:opacity-60"
                    : "rounded-full border border-teal px-4 py-2 text-xs font-bold text-teal hover:bg-teal hover:text-white disabled:opacity-60"
                }
              >
                {followBusy ? "Updating..." : following ? "Following ✓" : "Follow"}
              </button>
              {followersCount !== null && (
                <span id={`followers-count-${workshopId}`} className="text-sm text-ink/70">
                  {followersCount} {followersCount === 1 ? "follower" : "followers"}
                </span>
              )}
              {followMessage && (
                <span
                  id={`follow-message-${workshopId}`}
                  role="status"
                  className="text-sm font-semibold text-teal"
                >
                  {followMessage}
                </span>
              )}
            </div>
          )}
          {detail.description && (
            <p
              id="workshop-detail-description"
              className="mt-6 max-w-2xl text-base leading-7 text-ink/75"
            >
              {detail.description}
            </p>
          )}
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
                {statusLabel}
              </span>
            </div>
            <div className="mt-8 h-2 overflow-hidden rounded-full bg-white/15">
              <div
                className="h-full rounded-full bg-coral transition-all"
                style={{
                  width: spots === 0 ? "0%" : String((spots / detail.max_capacity) * 100) + "%",
                }}
              />
            </div>
            <p className="mt-5 text-sm leading-6 text-paper/80">
              Reserve a place, then bring your questions. We keep these sessions small so there is
              room to participate.
            </p>
          </section>

          {ended ? (
            <section
              id="workshop-detail-ended-card"
              className="rounded-[2rem] border border-line bg-sand/60 p-6 sm:p-9"
            >
              <p className="text-sm font-bold uppercase tracking-[.18em] text-ink/60">
                Session over
              </p>
              <h2 className="display-font mt-4 text-2xl font-bold">This session has ended.</h2>
              <p className="mt-3 text-sm leading-6 text-ink/75">
                Registration is closed and the workshop has finished. Browse the calendar for
                upcoming sessions.
              </p>
            </section>
          ) : registrationClosed ? (
            <section
              id="workshop-detail-closed-card"
              className="rounded-[2rem] border border-soft-edge bg-soft p-6 sm:p-9"
            >
              <p className="text-sm font-bold uppercase tracking-[.18em] text-coral">
                Registration closed
              </p>
              <h2 className="display-font mt-4 text-2xl font-bold">
                The booking window has closed.
              </h2>
              <p className="mt-3 text-sm leading-6 text-ink/75">
                Registration for this session closed on {formatDate(registrationDeadline)}. Keep an
                eye on the calendar for future sessions.
              </p>
            </section>
          ) : full && confirmation === null ? (
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
              {detail.waitlist_position !== null ? (
                waitlistStatus
              ) : hasSeat ? (
                <p className="mt-6 text-sm font-semibold text-ink/75">
                  You already have a seat in this session.
                </p>
              ) : (
                <button
                  id={`join-waitlist-${workshopId}`}
                  type="button"
                  disabled={waitlistBusy}
                  onClick={() => void handleJoinWaitlist()}
                  className="mt-6 flex w-full items-center justify-center gap-3 rounded-xl bg-coral px-5 py-3.5 font-bold text-white shadow-lg shadow-coral/20 transition hover:bg-coral-dark disabled:cursor-wait disabled:opacity-60 sm:w-auto"
                >
                  {waitlistBusy ? "Joining..." : "Join waitlist"} <span aria-hidden="true">→</span>
                </button>
              )}
            </section>
          ) : (
            <section
              id="workshop-detail-reserve-card"
              className="rounded-[2rem] border border-line bg-paper/90 p-6 shadow-lg shadow-ink/5 sm:p-9"
            >
              {confirmation ? (
                <ConfirmationPanel reservation={confirmation} />
              ) : (
                <>
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
                      onReserved={handleReserved}
                      onOpenAuth={() => setAuthOpen(true)}
                    />
                  </div>
                  {waitlistStatus}
                </>
              )}
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

        <section id="workshop-detail-reviews" className="mt-14">
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line pb-5">
            <div>
              <p className="text-sm font-bold uppercase tracking-[.18em] text-teal">Reviews</p>
              <h2 className="display-font mt-2 text-3xl font-bold tracking-tight">
                What attendees say
              </h2>
            </div>
            {detail.rating_average !== null && (
              <span id="workshop-detail-rating-aggregate" className="text-sm text-ink/70">
                {detail.rating_average.toFixed(1)} · {detail.rating_count}{" "}
                {detail.rating_count === 1 ? "review" : "reviews"}
              </span>
            )}
          </div>
          {canReview && (
            <form
              id="review-form"
              onSubmit={handleSubmitReview}
              className="mt-6 rounded-3xl border border-line bg-paper/80 p-6"
            >
              <p className="text-sm font-semibold">Review this session</p>
              <div role="radiogroup" aria-label="Rating" className="mt-3 flex gap-2">
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    id={`review-rating-${star}`}
                    type="button"
                    role="radio"
                    aria-checked={reviewRating === star}
                    onClick={() => setReviewRating(star)}
                    className={
                      "grid h-10 w-10 place-items-center rounded-full border text-sm font-bold transition " +
                      (reviewRating === star
                        ? "border-teal bg-teal text-white"
                        : "border-line bg-white text-ink/75 hover:border-teal/60 hover:text-teal")
                    }
                  >
                    {star}
                  </button>
                ))}
              </div>
              <label htmlFor="review-text" className="sr-only">
                Your review (optional)
              </label>
              <textarea
                id="review-text"
                rows={3}
                maxLength={4000}
                value={reviewText}
                onChange={(event) => setReviewText(event.target.value)}
                placeholder="What did you take away?"
                className="mt-3 w-full rounded-xl border border-line bg-white px-4 py-3"
              />
              <button
                id="review-submit-button"
                type="submit"
                disabled={reviewSubmitting}
                className="mt-4 rounded-full bg-teal px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:opacity-60"
              >
                {reviewSubmitting ? "Posting..." : "Post review"}
              </button>
              {reviewError && (
                <p
                  id="review-form-error"
                  role="alert"
                  className="mt-4 rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
                >
                  {reviewError}
                </p>
              )}
            </form>
          )}
          {user && hasReviewed && (
            <p id="review-already-posted" className="mt-6 text-sm font-semibold text-ink/70">
              You&apos;ve reviewed this session.
            </p>
          )}
          {detail.reviews.length === 0 ? (
            <p id="review-empty" className="mt-6 text-sm text-ink/70">
              No reviews yet.
            </p>
          ) : (
            <ul id="workshop-detail-reviews-list" className="mt-6 space-y-3">
              {detail.reviews.map((review) => (
                <li
                  key={review.id}
                  id={`workshop-detail-review-${review.id}`}
                  className="rounded-2xl border border-line bg-paper/70 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="font-semibold">{review.user_name}</p>
                    <span
                      id={`review-rating-badge-${review.id}`}
                      className="rounded-full bg-sand px-3 py-1 text-xs font-bold text-ink"
                    >
                      {review.rating}/5
                    </span>
                  </div>
                  {review.text && (
                    <p className="mt-2 text-sm leading-6 text-ink/75">{review.text}</p>
                  )}
                  <p className="mt-2 text-xs text-ink/60">{formatDate(review.created_at)}</p>
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
              const cancelledId = pendingCancelId;
              await cancel(cancelledId);
              // A cancelled booking's confirmation panel is
              // stale the moment the seat is released.
              if (confirmation?.id === cancelledId) {
                setConfirmation(null);
              }
              setPendingCancelId(null);
            }
          }}
          onCancel={() => setPendingCancelId(null)}
        />
        <ConfirmDialog
          open={pendingLeaveWaitlist}
          title="Leave the waitlist?"
          description="You'll lose your place in line. You can rejoin while the session is still full."
          confirmLabel="Yes, leave the waitlist"
          busy={leavingWaitlist}
          onConfirm={() => void handleLeaveWaitlist()}
          onCancel={() => setPendingLeaveWaitlist(false)}
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
