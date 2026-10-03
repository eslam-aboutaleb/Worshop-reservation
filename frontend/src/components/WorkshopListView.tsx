/**
 * List view: catalogue of all workshops with current spot counts.
 *
 * Data flow:
 * - `useWorkshopList` fetches the paginated catalogue on
 *   mount and whenever the discovery filters (search term,
 *   category chip, state tab) or `refreshKey` change. The
 *   search term is debounced inside the hook.
 * - `loadMore` appends the next page of the envelope; the
 *   button hides once every matching session is loaded.
 * - The list re-renders when the parent passes a new entry in
 *   `spotOverrides` (an SSE update) so live spot changes show up
 *   without a re-fetch.
 * - Selecting a workshop calls `onSelect(id)`, which updates the URL.
 *
 * Auth UX: the `AuthPanel` lives in the header; when the user is
 * anonymous the reserve CTA on the detail view will instead route
 * them back to the list and pop the auth modal open.
 */
import { useState } from "react";

import { useAuth } from "../features/auth/AuthContext";
import { useWorkshopList } from "../features/workshops/hooks/useWorkshopList";
import { AuthPanel } from "./AuthPanel";
import { useToast, useToastError } from "./Toast";
import { formatDate, formatTime } from "../utils/formatters";

/**
 * The catalogue's known topics.
 *
 * The list endpoint does not return a category per item, so
 * the chip set is fixed to the topics the catalogue uses
 * rather than derived from the loaded page.
 */
const CATEGORIES = ["Engineering", "Data"];

/** Timing tabs for the discovery filter. */
const STATE_TABS = [
  { value: "upcoming", label: "Upcoming" },
  { value: "past", label: "Past" },
  { value: "all", label: "All" },
] as const;

interface Props {
  /** Live spot-count overrides keyed by workshop id (from SSE). */
  spotOverrides: Record<string, number>;
  /** Called when the user picks a workshop. */
  onSelect: (id: string) => void;
  /** Opens the account dashboard. */
  onOpenAccount: () => void;
  /** Opens the printable tickets page. */
  onOpenTickets: () => void;
  /** Opens the admin session manager (admin only). */
  onOpenAdmin: () => void;
  /** Opens the organizer dashboard (organizers only). */
  onOpenOrganizer: () => void;
  /** When `true`, the auth modal opens on first render. */
  authRequested?: boolean;
  /** Incremented after workshop create/delete events. */
  refreshKey: number;
}

/**
 * Render the workshop catalogue and the marketing header.
 *
 * The view is self-contained: it only needs the live `spotOverrides`
 * map and a callback to navigate to the detail view.
 */
export function WorkshopListView({
  spotOverrides,
  onSelect,
  onOpenAccount,
  onOpenTickets,
  onOpenAdmin,
  onOpenOrganizer,
  authRequested,
  refreshKey,
}: Props) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [searchTerm, setSearchTerm] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [state, setState] = useState<(typeof STATE_TABS)[number]["value"]>("upcoming");
  const [following, setFollowing] = useState(false);
  const { workshops, total, error, setError, loading, loadingMore, loadMore, hasMore } =
    useWorkshopList(refreshKey, {
      q: searchTerm,
      category: category ?? undefined,
      state,
      following,
    });
  useToastError(error, () => setError(null));
  const now = new Date();

  /**
   * Toggle the "Following" discovery filter.
   *
   * The filter is signed-in-only: the backend answers
   * an anonymous `following=true` request with a 401,
   * so an anonymous click is intercepted here with a
   * toast prompting sign-in instead of firing a
   * request that is guaranteed to fail.
   */
  function handleToggleFollowing() {
    if (!user) {
      showToast("Sign in to see workshops from organizations you follow.", "info");
      return;
    }
    setFollowing((value) => !value);
  }

  return (
    <div id="workshop-list-view" className="min-h-screen">
      <header
        id="workshop-list-header"
        className="mx-auto flex max-w-6xl items-center justify-between px-5 py-6 sm:px-8"
      >
        <button
          id="workshop-list-logo"
          type="button"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          aria-label="Back to top"
          className="display-font flex items-center gap-2 text-lg font-bold tracking-tight"
        >
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-ink text-lg text-paper">
            W
          </span>
          Workshop
        </button>
        <div className="flex items-center gap-3">
          {user && (
            <button
              id="my-reservations-button"
              type="button"
              onClick={onOpenAccount}
              className="hidden text-xs font-bold uppercase tracking-wider text-teal hover:text-coral sm:block"
            >
              My reservations
            </button>
          )}
          {user && (
            <button
              id="my-tickets-button"
              type="button"
              onClick={onOpenTickets}
              className="hidden text-xs font-bold uppercase tracking-wider text-teal hover:text-coral sm:block"
            >
              My tickets
            </button>
          )}
          {user?.is_admin && (
            <button
              id="admin-nav-button"
              type="button"
              onClick={onOpenAdmin}
              className="hidden text-xs font-bold uppercase tracking-wider text-teal hover:text-coral sm:block"
            >
              Admin
            </button>
          )}
          {(user?.role === "organizer" || user?.role === "admin") && (
            <button
              id="organizer-nav-button"
              type="button"
              onClick={onOpenOrganizer}
              className="hidden text-xs font-bold uppercase tracking-wider text-teal hover:text-coral sm:block"
            >
              Organizer
            </button>
          )}
          <AuthPanel initialOpen={authRequested} />
        </div>
      </header>

      <section
        id="workshop-list-hero"
        className="mx-auto grid max-w-6xl gap-10 px-5 pb-16 pt-8 sm:px-8 lg:grid-cols-[1.1fr_.9fr] lg:items-end lg:pt-16"
      >
        <div className="fade-up">
          <p className="mb-5 text-sm font-bold uppercase tracking-[.2em] text-coral">
            Learn something worth sharing
          </p>
          <h1 className="display-font max-w-3xl text-5xl font-bold leading-[.98] tracking-[-.06em] sm:text-7xl">
            Make room for
            <br />
            <span className="text-coral">curiosity.</span>
          </h1>
          <p className="mt-7 max-w-xl text-lg leading-8 text-ink/70">
            Small, practical workshops for people who like to leave with new ideas and useful
            skills.
          </p>
        </div>
        <div className="relative overflow-hidden rounded-[2rem] bg-ink p-7 text-paper shadow-xl shadow-ink/10 sm:p-9">
          <div className="absolute -right-12 -top-16 h-48 w-48 rounded-full border-[18px] border-coral/80" />
          <div className="relative">
            <p className="text-sm font-semibold uppercase tracking-[.18em] text-teal-light">
              Your next session
            </p>
            <p className="display-font mt-12 max-w-xs text-3xl font-semibold leading-tight">
              One good afternoon can change your whole week.
            </p>
            <div className="mt-8 h-px bg-paper/20" />
            <p className="mt-4 text-sm text-paper/80">
              Browse the calendar below and save your seat.
            </p>
          </div>
        </div>
      </section>

      <section id="workshop-list-calendar" className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <div className="mb-7 space-y-5 border-b border-line pb-5">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-sm font-bold uppercase tracking-[.18em] text-teal">The calendar</p>
              <h2 className="display-font mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
                Find your workshop
              </h2>
            </div>
            <span
              id="workshop-list-count"
              className="hidden text-sm text-ink/70 sm:block"
              aria-live="polite"
            >
              {loading
                ? "Loading workshops..."
                : total > workshops.length
                  ? `Showing ${workshops.length} of ${total} sessions`
                  : `${total} sessions available`}
            </span>
          </div>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div className="w-full sm:flex-1">
              <label htmlFor="workshop-list-search-input" className="sr-only">
                Search workshops
              </label>
              <input
                id="workshop-list-search-input"
                type="search"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search by title or topic"
                className="w-full rounded-full border border-line bg-paper/80 px-5 py-3 outline-none placeholder:text-ink/65 focus:border-teal"
              />
            </div>
            <div
              id="workshop-list-state-tabs"
              role="tablist"
              aria-label="Session timing"
              className="flex gap-2"
            >
              {STATE_TABS.map((tab) => (
                <button
                  key={tab.value}
                  id={`workshop-list-state-tab-${tab.value}`}
                  type="button"
                  role="tab"
                  aria-selected={state === tab.value}
                  onClick={() => setState(tab.value)}
                  className={
                    "rounded-full px-4 py-2 text-sm font-semibold transition " +
                    (state === tab.value
                      ? "bg-ink text-paper"
                      : "border border-line bg-paper/80 text-ink/75 hover:border-teal/60 hover:text-teal")
                  }
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
          <div id="workshop-list-categories" className="flex flex-wrap gap-2">
            <button
              id="workshop-list-category-all"
              type="button"
              aria-pressed={category === null}
              onClick={() => setCategory(null)}
              className={
                "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition " +
                (category === null
                  ? "border-teal bg-teal text-white"
                  : "border-line bg-paper/80 text-ink/75 hover:border-teal/60 hover:text-teal")
              }
            >
              All topics
            </button>
            {CATEGORIES.map((topic) => (
              <button
                key={topic}
                id={`workshop-list-category-${topic.toLowerCase()}`}
                type="button"
                aria-pressed={category === topic}
                onClick={() => setCategory(category === topic ? null : topic)}
                className={
                  "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition " +
                  (category === topic
                    ? "border-teal bg-teal text-white"
                    : "border-line bg-paper/80 text-ink/75 hover:border-teal/60 hover:text-teal")
                }
              >
                {topic}
              </button>
            ))}
            <button
              id="workshop-list-following-chip"
              type="button"
              aria-pressed={following}
              onClick={handleToggleFollowing}
              title={
                user
                  ? "Show workshops from organizations you follow"
                  : "Sign in to filter by followed organizations"
              }
              className={
                "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition " +
                (following
                  ? "border-coral bg-coral text-white"
                  : "border-line bg-paper/80 text-ink/75 hover:border-coral/60 hover:text-coral")
              }
            >
              Following
            </button>
          </div>
        </div>
        {loading ? (
          <div
            id="workshop-list-loading"
            className="grid gap-4 md:grid-cols-2 lg:grid-cols-3"
            aria-label="Loading workshops"
          >
            {[1, 2, 3].map((item) => (
              <div key={item} className="h-72 animate-pulse rounded-3xl bg-sand" />
            ))}
          </div>
        ) : workshops.length === 0 ? (
          <div
            id="workshop-list-empty"
            className="rounded-3xl border border-dashed border-line bg-paper/60 px-6 py-16 text-center"
          >
            <p className="display-font text-2xl font-semibold">A quiet calendar, for now.</p>
            <p className="mt-2 text-ink/70">Check back soon for new sessions.</p>
          </div>
        ) : (
          <>
            <ul id="workshop-list-grid" className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {workshops.map((workshop, index) => {
                const spots = spotOverrides[workshop.id] ?? workshop.available_spots;
                const full = spots === 0;
                const ended = workshop.ends_at !== null && new Date(workshop.ends_at) < now;
                const registrationDeadline = workshop.registration_closes_at ?? workshop.starts_at;
                const registrationClosed = new Date(registrationDeadline) < now;
                const badgeLabel = ended
                  ? "Ended"
                  : registrationClosed
                    ? "Registration closed"
                    : full
                      ? "Waitlist soon"
                      : "Open seats";
                const badgeClass = ended
                  ? "rounded-full bg-sand px-3 py-1 font-semibold text-ink/70"
                  : registrationClosed
                    ? "rounded-full bg-soft px-3 py-1 font-semibold text-danger"
                    : "rounded-full bg-teal-light px-3 py-1 font-semibold text-teal";
                return (
                  <li
                    key={workshop.id}
                    id={`workshop-list-item-${workshop.id}`}
                    className="fade-up"
                    style={{ animationDelay: String(Math.min(index, 5) * 80) + "ms" }}
                  >
                    <article
                      id={`workshop-list-card-${workshop.id}`}
                      className="group flex h-full flex-col rounded-3xl border border-line bg-paper/80 p-6 transition duration-300 hover:-translate-y-1 hover:border-teal/50 hover:bg-white hover:shadow-xl hover:shadow-ink/5"
                    >
                      <div className="flex items-center justify-between text-sm">
                        <span id={`workshop-list-status-${workshop.id}`} className={badgeClass}>
                          {badgeLabel}
                        </span>
                        <span className="font-semibold text-ink/55">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                      </div>
                      <h3
                        id={`workshop-list-title-${workshop.id}`}
                        className="display-font mt-8 min-h-20 text-2xl font-bold leading-tight tracking-tight"
                      >
                        {workshop.title}
                      </h3>
                      <div className="mt-auto space-y-3 border-t border-line pt-5 text-sm text-ink/75">
                        <p className="flex items-center gap-2">
                          <span aria-hidden="true" className="text-teal">
                            ◫
                          </span>{" "}
                          {formatDate(workshop.starts_at)} <span className="text-ink/65">at</span>{" "}
                          {formatTime(workshop.starts_at)}
                        </p>
                        <div className="flex items-end justify-between gap-4">
                          <p>
                            <span
                              id={`workshop-list-spots-${workshop.id}`}
                              className={
                                "display-font text-3xl font-bold " +
                                (full ? "text-coral" : "text-teal-bright")
                              }
                            >
                              {spots}
                            </span>{" "}
                            <span className="text-xs uppercase tracking-wider">seats left</span>
                          </p>
                          <button
                            id={`workshop-list-explore-button-${workshop.id}`}
                            type="button"
                            onClick={() => onSelect(workshop.id)}
                            className="rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-paper transition hover:bg-teal focus-visible:outline-2"
                          >
                            Explore <span aria-hidden="true">→</span>
                          </button>
                        </div>
                      </div>
                    </article>
                  </li>
                );
              })}
            </ul>
            {hasMore && (
              <div className="mt-8 text-center">
                <button
                  id="workshop-list-load-more"
                  type="button"
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="rounded-full border border-line bg-paper/80 px-6 py-3 text-sm font-bold text-ink transition hover:border-teal/60 hover:text-teal disabled:opacity-60"
                >
                  {loadingMore
                    ? "Loading more sessions..."
                    : `Load more (${total - workshops.length} remaining)`}
                </button>
              </div>
            )}
          </>
        )}
      </section>
      <footer
        id="workshop-list-footer"
        className="border-t border-line px-5 py-7 text-center text-sm text-ink/70"
      >
        Thoughtful sessions. Limited seats. Better conversations.
      </footer>
    </div>
  );
}
