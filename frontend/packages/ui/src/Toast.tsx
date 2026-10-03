import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { ApiError } from "@ws/api-client";

type ToastKind = "success" | "error" | "info";

type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
};

type ToastContextValue = {
  showToast: (message: string, kind?: ToastKind) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Monotonic counter so two toasts issued in the same millisecond
  // still get distinct ids. (Replaces the old `Date.now() + Math.random()`
  // pattern that could collide under StrictMode double-fires.)
  const idCounter = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((items) => items.filter((toast) => toast.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, kind: ToastKind = "info") => {
      const id = ++idCounter.current;
      setToasts((items) => [...items.slice(-2), { id, kind, message }]);
      window.setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {/*
        The region advertises both politeness levels: success/info
        are polite, errors interrupt. Each individual toast carries
        its own `role` so screen readers announce the right
        urgency per message, and `aria-atomic` is set per-item
        instead of on the container so a new toast doesn't
        re-read the whole region.
      */}
      <div
        id="toast-region"
        className="pointer-events-none fixed inset-x-4 top-4 z-50 flex flex-col items-end gap-3 sm:left-auto sm:right-6 sm:w-[min(28rem,calc(100vw-3rem))]"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.kind === "error" ? "alert" : "status"}
            aria-live={toast.kind === "error" ? "assertive" : "polite"}
            aria-atomic="true"
            className={
              "pointer-events-auto flex w-full items-start gap-3 rounded-2xl border px-4 py-3 text-sm shadow-xl " +
              (toast.kind === "success"
                ? "border-success-edge bg-success-soft text-success"
                : toast.kind === "error"
                  ? "border-soft-edge bg-soft text-danger"
                  : "border-line bg-paper text-ink")
            }
          >
            <span aria-hidden="true" className="mt-0.5 font-bold">
              {toast.kind === "success" ? "✓" : toast.kind === "error" ? "!" : "i"}
            </span>
            <span className="flex-1 leading-5">{toast.message}</span>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
              className="font-bold opacity-60 hover:opacity-100"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
}

/** Convert known API failures into concise user-facing toast copy. */
export function getToastErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "workshop_full") {
      return "That last seat was just taken. Availability has been refreshed.";
    }
    if (error.code === "already_reserved") {
      return "You already reserved this workshop. Manage it from My reservations.";
    }
    if (error.code === "workshop_has_active_reservations") {
      return "This session still has active reservations. Ask attendees to cancel first.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

/** Show an error exactly once, including under React StrictMode effects. */
export function useToastError(error: unknown, onClear: () => void): void {
  const { showToast } = useToast();
  const lastError = useRef<unknown>(null);
  const message = getToastErrorMessage(error);

  useEffect(() => {
    if (!error) {
      lastError.current = null;
      return;
    }
    if (lastError.current === error) return;
    lastError.current = error;
    showToast(message, "error");
    onClear();
  }, [error, message, onClear, showToast]);
}
