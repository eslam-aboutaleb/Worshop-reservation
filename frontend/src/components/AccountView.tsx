/**
 * Account dashboard: lists the signed-in user's reservations
 * (active and cancelled) with their workshop titles, and exposes
 * an inline cancel control for each active entry. Active waitlist
 * entries (position + workshop title) are listed below the
 * reservations.
 *
 * Local state is updated optimistically on cancel so the list feels
 * instant; the `cancelled_at` timestamp is faked to "now" client-
 * side because the response body is the cancel ack, not a refetch.
 * The next visit to the dashboard will show the server-side value.
 *
 * The "Create organization" form is the entry point into the
 * organizer platform: the signed-in caller becomes the new
 * organization's first owner and is promoted to the `organizer`
 * platform role in the same transaction. The session is rehydrated
 * (`getMe` + `setSession`) on success so the Organizer nav item
 * appears without a reload.
 */
import { useState } from "react";

import { ApiError, createOrganization, getMe } from "../api";
import { useAuth } from "../features/auth/AuthContext";
import { useMyReservations } from "../features/reservations/hooks/useMyReservations";
import { useMyWaitlistEntries } from "../features/reservations/hooks/useMyWaitlistEntries";
import type { Organization } from "../types";
import { useToast, useToastError, getToastErrorMessage } from "./Toast";
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
  const { setSession } = useAuth();
  const { reservations, error, setError, loading, cancel } = useMyReservations();
  const {
    entries: waitlistEntries,
    error: waitlistError,
    setError: setWaitlistError,
    loading: waitlistLoading,
  } = useMyWaitlistEntries();
  useToastError(error, () => setError(null));
  useToastError(waitlistError, () => setWaitlistError(null));
  const { showToast } = useToast();
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);

  // Organization creation form state.
  const [orgName, setOrgName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [orgSubmitting, setOrgSubmitting] = useState(false);
  const [orgFormError, setOrgFormError] = useState<string | null>(null);
  const [createdOrg, setCreatedOrg] = useState<Organization | null>(null);

  /**
   * Create an organization from the account form.
   *
   * On success the created organization (with the
   * creator's `owner` membership) is rendered inline
   * and the session is rehydrated so the platform-
   * role promotion to `organizer` is reflected
   * immediately.
   */
  async function handleCreateOrganization(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOrgFormError(null);
    const name = orgName.trim();
    if (!name) {
      setOrgFormError("Give your organization a name.");
      return;
    }
    setOrgSubmitting(true);
    try {
      const created = await createOrganization({
        name,
        slug: orgSlug.trim() || undefined,
      });
      setCreatedOrg(created);
      // The promotion to `organizer` happens in the same
      // transaction as the create; rehydrate the session
      // so the Organizer nav item appears without a reload.
      const fresh = await getMe();
      setSession(fresh);
      showToast("Organization created. You're an organizer now.", "success");
    } catch (requestError) {
      setOrgFormError(
        requestError instanceof ApiError
          ? getToastErrorMessage(requestError)
          : "Failed to create the organization.",
      );
    } finally {
      setOrgSubmitting(false);
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
        {!waitlistLoading && waitlistEntries.length > 0 && (
          <section id="account-waitlist" className="mt-10">
            <p className="text-xs font-bold uppercase tracking-[.18em] text-coral">Waitlist</p>
            <h2 className="display-font mt-2 text-3xl font-bold">Your waitlist</h2>
            <p className="mt-2 text-sm text-ink/70">
              Places in line for full sessions. When a seat opens, the next person in line is booked
              automatically.
            </p>
            <ul className="mt-6 space-y-3">
              {waitlistEntries.map((entry) => (
                <li
                  key={entry.id}
                  id={`account-waitlist-entry-${entry.id}`}
                  className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-line bg-paper/80 p-4"
                >
                  <div>
                    <p className="font-semibold">{entry.workshop_title}</p>
                    <p className="mt-1 text-xs text-ink/70">
                      Joined {formatDate(entry.created_at)}
                    </p>
                  </div>
                  <span
                    id={`account-waitlist-position-${entry.id}`}
                    className="rounded-full bg-sand px-4 py-2 text-xs font-bold text-ink"
                  >
                    #{entry.position} in line
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section id="account-organizations" className="mt-10">
          <p className="text-xs font-bold uppercase tracking-[.18em] text-coral">Organization</p>
          <h2 className="display-font mt-2 text-3xl font-bold">Create an organization</h2>
          <p className="mt-2 text-sm text-ink/70">
            Run workshops under a name your attendees can follow. You become the organization&apos;s
            first owner and an organizer.
          </p>
          {createdOrg && (
            <div
              id="account-created-organization"
              className="mt-6 rounded-3xl border border-teal/40 bg-teal-light/45 p-6"
            >
              <p className="text-xs font-bold uppercase tracking-wider text-teal">
                Your organization
              </p>
              <p
                id="account-created-organization-name"
                className="display-font mt-2 text-2xl font-bold"
              >
                {createdOrg.name}
              </p>
              <p className="mt-2 text-sm text-ink/75">
                <span className="text-ink/60">Slug:</span>{" "}
                <span id="account-created-organization-slug" className="font-mono font-semibold">
                  {createdOrg.slug}
                </span>
              </p>
              <p className="mt-2 text-sm font-semibold text-teal">You own this organization.</p>
            </div>
          )}
          <form
            id="account-create-organization-form"
            onSubmit={handleCreateOrganization}
            className="mt-6 grid gap-4 sm:grid-cols-2"
          >
            <label className="text-sm font-semibold">
              Organization name
              <input
                id="account-organization-name"
                required
                minLength={1}
                maxLength={200}
                value={orgName}
                onChange={(event) => setOrgName(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="Neighborhood Kitchen Collective"
              />
            </label>
            <label className="text-sm font-semibold">
              Slug
              <input
                id="account-organization-slug-input"
                maxLength={200}
                value={orgSlug}
                onChange={(event) => setOrgSlug(event.target.value)}
                className="mt-2 w-full rounded-xl border border-line bg-white px-4 py-3"
                placeholder="Optional — derived from the name"
              />
              <span className="mt-1 block text-xs font-normal text-ink/70">
                Optional URL-safe identifier.
              </span>
            </label>
            <div className="sm:col-span-2">
              <button
                id="account-create-organization-button"
                type="submit"
                disabled={orgSubmitting}
                className="rounded-full bg-teal px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:opacity-60"
              >
                {orgSubmitting ? "Creating..." : "Create organization"}
              </button>
            </div>
            {orgFormError && (
              <p
                id="account-organization-form-error"
                className="sm:col-span-2 rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
                role="alert"
              >
                {orgFormError}
              </p>
            )}
          </form>
        </section>
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
      </div>
    </div>
  );
}
