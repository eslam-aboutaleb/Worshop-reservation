/**
 * Top-level render error boundary.
 *
 * Catches uncaught render-time exceptions so the SPA never shows a
 * blank page. The fallback uses the same brand tokens as the rest of
 * the app (paper background, ink + coral text) and offers a single
 * way out: reload.
 *
 * Error reports are forwarded to the optional `onError` callback so
 * the owning app decides where they land (a structured logger, a
 * remote reporter, …). When no callback is supplied the error is
 * logged with `console.error`.
 */
import React from "react";

interface Props {
  children: React.ReactNode;
  /**
   * Called with every caught render error. The app passes its
   * structured logger here; the default is `console.error`.
   */
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface State {
  hasError: boolean;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(_: Error): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    } else {
      console.error("React component boundary caught error", { error, errorInfo });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          id="app-error-boundary"
          className="paper-grid flex min-h-screen items-center justify-center px-5 py-12"
        >
          <div className="w-full max-w-md rounded-[2rem] border border-soft-edge bg-soft p-8 text-center shadow-xl shadow-ink/5 sm:p-10">
            <p className="text-sm font-bold uppercase tracking-[.18em] text-coral">
              Something went sideways
            </p>
            <h1 className="display-font mt-4 text-3xl font-bold text-ink">
              The page hit an unexpected snag.
            </h1>
            <p className="mt-3 text-sm leading-6 text-ink/75">
              The error has been logged. A fresh load usually clears it.
            </p>
            <button
              id="app-error-boundary-reload-button"
              type="button"
              onClick={() => window.location.reload()}
              className="mt-7 inline-flex items-center justify-center rounded-full bg-coral px-6 py-3 text-sm font-bold text-white shadow-lg shadow-coral/20 transition hover:bg-coral-dark"
            >
              Reload the page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
