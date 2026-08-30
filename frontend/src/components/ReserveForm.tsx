/**
 * Reservation form embedded in the workshop detail view.
 *
 * Anonymous visitors see a "sign in to reserve" prompt. Signed-in
 * users see a single Reserve button; the request is sent with the
 * account's email and a fresh `Idempotency-Key` generated on the
 * first render and rotated after every successful submit.
 *
 * Idempotency strategy: the key is stored in a `useRef` and survives
 * re-renders, so a React re-render does not generate a new key and
 * turn a real retry into a fresh request. The key is only rotated
 * after a successful 201.
 *
 * On a successful reservation the form surfaces a success toast
 * and the parent re-fetches the detail. The "Booked" status and
 * the user's reservation row under "The room" are the persistent
 * confirmation; an in-form banner is intentionally avoided so
 * the success state never goes stale after a later cancel.
 */
import { useRef, useState } from "react";

import { createReservation } from "../api";
import { useAuth } from "../features/auth/AuthContext";
import { useToast } from "./Toast";

interface Props {
  /** Workshop to reserve against. */
  workshopId: string;
  /** Called after a successful 201 so the parent can refresh. */
  onReserved: () => void;
  /** Called when the anonymous visitor chooses to sign in. */
  onOpenAuth: () => void;
}

export function ReserveForm({ workshopId, onReserved, onOpenAuth }: Props) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const idempotencyKey = useRef(crypto.randomUUID());

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) {
      onOpenAuth();
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await createReservation(workshopId, user.full_name, user.email, idempotencyKey.current);
      // Rotate the key on success so the next explicit submit starts
      // a new logical request.
      idempotencyKey.current = crypto.randomUUID();
      onReserved();
      showToast("Your seat is reserved. We'll see you there!", "success");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to reserve a seat");
    } finally {
      setSubmitting(false);
    }
  }

  return user ? (
    <form id="reserve-form" onSubmit={handleSubmit} className="space-y-5">
      {formError && (
        <p
          className="rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
          role="alert"
        >
          {formError}
        </p>
      )}
      <button
        id="reserve-submit-button"
        type="submit"
        disabled={submitting}
        className="flex w-full items-center justify-center gap-3 rounded-xl bg-coral px-5 py-3.5 font-bold text-white shadow-lg shadow-coral/20 transition hover:bg-coral-dark disabled:cursor-wait disabled:opacity-60 sm:w-auto"
      >
        {submitting ? "Saving your seat..." : "Reserve my seat"}{" "}
        <span aria-hidden="true">→</span>
      </button>
      <p className="text-xs leading-5 text-ink/70">
        Reserving as {user.email}. Your account will keep this reservation available on every
        device.
      </p>
    </form>
  ) : (
    <div
      id="reserve-anonymous-prompt"
      className="rounded-2xl bg-teal-light p-5 text-sm text-ink/75"
    >
      <p className="font-semibold text-ink">Sign in to save and manage your seat.</p>
      <button
        id="reserve-signin-button"
        type="button"
        onClick={onOpenAuth}
        className="mt-4 rounded-full bg-teal px-4 py-2.5 font-bold text-white transition hover:bg-ink"
      >
        Sign in or create an account →
      </button>
    </div>
  );
}
