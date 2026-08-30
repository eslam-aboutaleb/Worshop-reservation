/**
 * 404 / unknown-route view.
 *
 * Reached when the URL doesn't match the home, account, or workshop
 * detail patterns. Keeps the same brand shell (paper background, ink
 * hero card) and offers a single way out: return to the catalogue.
 */
interface Props {
  onHome: () => void;
}

export function NotFoundView({ onHome }: Props) {
  return (
    <div
      id="not-found-view"
      className="paper-grid flex min-h-screen items-center justify-center px-5 py-12"
    >
      <div className="w-full max-w-md rounded-[2rem] bg-ink p-8 text-center text-paper shadow-xl shadow-ink/10 sm:p-10">
        <p className="text-sm font-bold uppercase tracking-[.18em] text-coral-bright">
          Page not found
        </p>
        <h1 className="display-font mt-4 text-5xl font-bold leading-none tracking-[-.06em]">404</h1>
        <p className="mt-3 text-sm leading-6 text-paper/80">
          The page you were looking for has wandered off. The calendar is one click away.
        </p>
        <button
          id="not-found-home-button"
          type="button"
          onClick={onHome}
          className="mt-7 inline-flex items-center justify-center gap-2 rounded-full bg-coral px-6 py-3 text-sm font-bold text-white shadow-lg shadow-coral/20 transition hover:bg-coral-dark"
        >
          <span aria-hidden="true">←</span> Back to workshops
        </button>
      </div>
    </div>
  );
}
