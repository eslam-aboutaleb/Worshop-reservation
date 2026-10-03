/**
 * Reusable confirmation dialog.
 *
 * Renders a centered modal that asks the user to confirm a
 * destructive or hard-to-reverse action before it runs. Returned as
 * ``null`` when no confirmation is in flight, so callers can drop
 * it next to the action button without a guard.
 *
 * Usage:
 *
 *   const [pending, setPending] = useState<string | null>(null);
 *   <button onClick={() => setPending(id)}>Cancel</button>
 *   <ConfirmDialog
 *     open={pending !== null}
 *     title="Cancel this reservation?"
 *     description="Your seat will be released and offered to the next person."
 *     confirmLabel="Yes, cancel"
 *     onConfirm={async () => { await cancel(pending!); setPending(null); }}
 *     onCancel={() => setPending(null)}
 *   />
 *
 * Accessibility:
 * - Focus moves to the confirm button on open.
 * - A simple focus trap keeps Tab cycling inside the panel.
 * - Escape dismisses the dialog (unless the action is in flight).
 */
import { useEffect, useRef } from "react";

export interface Props {
  /** When true, the dialog is mounted. */
  open: boolean;
  /** Short headline shown above the description. */
  title: string;
  /** Body copy that explains what will happen. */
  description: string;
  /** Label for the destructive (coral) action button. */
  confirmLabel: string;
  /** Optional label override for the dismiss button. Defaults to "Keep it". */
  cancelLabel?: string;
  /** Disable the confirm button while an async action is running. */
  busy?: boolean;
  /** Called when the user confirms. */
  onConfirm: () => void | Promise<void>;
  /** Called when the user dismisses (Escape, backdrop click, cancel button). */
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Keep it",
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        onCancel();
        return;
      }
      if (event.key === "Tab") {
        // Cycle focus between the two action buttons; nothing else
        // inside the panel is focusable.
        const focusables = [confirmRef.current, cancelRef.current].filter(
          (node): node is HTMLButtonElement => node !== null,
        );
        if (focusables.length === 0) return;
        const active = document.activeElement as HTMLElement | null;
        const currentIndex = active ? focusables.indexOf(active as HTMLButtonElement) : -1;
        if (event.shiftKey) {
          const previous = focusables[(currentIndex <= 0 ? focusables.length : currentIndex) - 1];
          previous?.focus();
        } else {
          const next = focusables[(currentIndex + 1) % focusables.length];
          next?.focus();
        }
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      id="confirm-dialog-backdrop"
      className="fixed inset-0 z-30 grid place-items-center bg-overlay p-5"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={() => {
        if (!busy) onCancel();
      }}
    >
      <div
        ref={panelRef}
        id="confirm-dialog-panel"
        className="w-full max-w-md rounded-[2rem] bg-paper p-7 shadow-2xl sm:p-9"
        onClick={(event) => event.stopPropagation()}
      >
        <p className="text-sm font-bold uppercase tracking-[.18em] text-coral">Please confirm</p>
        <h2
          id="confirm-dialog-title"
          className="display-font mt-3 text-2xl font-bold tracking-tight"
        >
          {title}
        </h2>
        <p className="mt-3 text-sm leading-6 text-ink/75">{description}</p>
        <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            id="confirm-dialog-cancel-button"
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-full border border-line px-5 py-2.5 text-sm font-bold text-ink/75 transition hover:border-teal hover:text-teal disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            id="confirm-dialog-confirm-button"
            type="button"
            disabled={busy}
            onClick={() => void onConfirm()}
            className="rounded-full bg-coral px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-coral/20 transition hover:bg-coral-dark disabled:cursor-wait disabled:opacity-60"
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
