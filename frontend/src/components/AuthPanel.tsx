/**
 * Sign-in / sign-up modal used by the list view's auth button and
 * the workshop detail view's anonymous reserve prompt.
 *
 * Two usage modes:
 * - Controlled: pass `open` and `onClose` / `onSignedIn`. The detail
 *   view uses this so the modal opens in place without navigating.
 * - Uncontrolled: pass `initialOpen` and the panel renders its own
 *   trigger button (the "Sign in" header chip). The list view uses
 *   this so a redirect with `?auth=sign-in` can pop the modal.
 *
 * The internal signin/signup mode is always local so toggling does
 * not round-trip through the parent. On a successful login the
 * panel closes itself and calls `onSignedIn` if provided.
 */
import { useEffect, useState } from "react";

import { login, signup } from "../api";
import { useAuth } from "../features/auth/AuthContext";

interface Props {
  /** Controlled open state. When false the modal is unmounted. */
  open?: boolean;
  /** Called when the user dismisses the modal (Escape, X, backdrop). */
  onClose?: () => void;
  /** Called after a successful signup or login. */
  onSignedIn?: () => void;
  /**
   * Uncontrolled-only: open the modal once on mount. Used by the
   * list view when a redirect carries `?auth=sign-in` so the modal
   * pops without a separate click.
   */
  initialOpen?: boolean;
}

export function AuthPanel({
  open: controlledOpen,
  onClose,
  onSignedIn,
  initialOpen = false,
}: Props = {}) {
  const { user, setSession, signOut } = useAuth();
  // Support both controlled and uncontrolled usage so the header
  // chip can keep its own "click to open" state while the detail
  // view passes a controlled value.
  const [internalOpen, setInternalOpen] = useState(initialOpen);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : internalOpen;
  const setOpen = (next: boolean) => {
    if (!isControlled) setInternalOpen(next);
    if (!next) onClose?.();
  };

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      const result =
        mode === "signup"
          ? await signup(name.trim(), email.trim(), password)
          : await login(email.trim(), password);
      setSession(result.user);
      setPassword("");
      setOpen(false);
      onSignedIn?.();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  // The greeting chip is owned by the header. When the panel is
  // mounted as a controlled modal (detail view, etc.) the greeting
  // must NOT be rendered here, or it ends up floating in the
  // document flow at the end of the parent's JSX.
  if (user && !isControlled) {
    return (
      <div id="auth-greeting" className="flex items-center gap-3">
        <span className="hidden text-sm font-semibold text-ink/70 sm:block">
          Hi, {user.full_name.split(" ")[0]}
        </span>
        <button
          id="auth-signout-button"
          type="button"
          onClick={signOut}
          className="rounded-full bg-ink px-4 py-2 text-xs font-bold text-paper hover:opacity-90"
        >
          Sign out
        </button>
      </div>
    );
  }

  if (!open) {
    // Uncontrolled mode renders the trigger button so the chip
    // works when used in the header. Controlled callers (e.g. the
    // detail view) render only the modal portion.
    if (isControlled) return null;
    return (
      <button
        id="auth-signin-button"
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm font-bold text-ink underline decoration-coral decoration-2 underline-offset-4 hover:text-coral"
      >
        Sign in
      </button>
    );
  }

  return (
    <div
      id="auth-modal"
      className="fixed inset-0 z-20 grid place-items-center bg-overlay p-5"
      role="dialog"
      aria-modal="true"
    >
      <form
        id="auth-form"
        onSubmit={submit}
        className="w-full max-w-md rounded-[2rem] bg-paper p-7 shadow-2xl sm:p-9"
      >
        <div className="flex items-start justify-between gap-5">
          <div>
            <p className="text-sm font-bold uppercase tracking-[.18em] text-coral">
              Your Workshop account
            </p>
            <h2 className="display-font mt-3 text-3xl font-bold">
              {mode === "signup" ? "Make a home for your plans" : "Welcome back"}
            </h2>
          </div>
          <button
            id="auth-close-button"
            type="button"
            onClick={() => setOpen(false)}
            className="text-2xl text-ink/70"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="mt-8 space-y-4">
          {mode === "signup" && (
            <div className="flex flex-col gap-1">
              <label htmlFor="auth-name-input" className="text-xs font-bold text-ink/70">
                Full name
              </label>
              <input
                id="auth-name-input"
                type="text"
                required
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-xl border border-line bg-transparent px-4 py-3 outline-none placeholder:text-ink/65 focus:border-teal"
                placeholder="Jane Doe"
              />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <label htmlFor="auth-email-input" className="text-xs font-bold text-ink/70">
              Email address
            </label>
            <input
              id="auth-email-input"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-xl border border-line bg-transparent px-4 py-3 outline-none placeholder:text-ink/65 focus:border-teal"
              placeholder="jane@example.com"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="auth-password-input" className="text-xs font-bold text-ink/70">
              Password
            </label>
            <input
              id="auth-password-input"
              type="password"
              required
              minLength={8}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-xl border border-line bg-transparent px-4 py-3 outline-none placeholder:text-ink/65 focus:border-teal"
              placeholder="At least 8 characters"
            />
          </div>
        </div>

        {formError && (
          <p
            className="mt-6 rounded-xl border border-soft-edge bg-soft px-4 py-3 text-sm font-semibold text-danger"
            role="alert"
          >
            {formError}
          </p>
        )}
        <button
          id="auth-submit-button"
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-full bg-teal py-4 font-bold text-white hover:bg-ink disabled:opacity-60"
        >
          {busy ? "Please wait..." : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        <div className="mt-6 flex justify-center">
          <button
            id="auth-toggle-mode-button"
            type="button"
            onClick={() => {
              setMode(mode === "signup" ? "signin" : "signup");
              setFormError(null);
            }}
            className="text-sm font-semibold text-ink/70 underline hover:text-ink"
          >
            {mode === "signup" ? "Already have an account? Sign in" : "Need an account? Sign up"}
          </button>
        </div>
      </form>
    </div>
  );
}
