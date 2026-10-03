import { useCallback, useEffect, useRef, useState } from "react";

import { listWorkshops } from "../../../api";
import type { Workshop } from "../../../types";

/** Page size requested from the discovery endpoint. */
const PAGE_SIZE = 20;
/** Debounce window for the search term, in milliseconds. */
const SEARCH_DEBOUNCE_MS = 250;

/** Discovery filters applied to the workshop catalogue. */
export interface WorkshopListFilters {
  /** Case-insensitive substring matched against title and description. */
  q?: string;
  /** Exact category filter. */
  category?: string;
  /** Upcoming (the default), past, or all sessions. */
  state?: "upcoming" | "past" | "all";
}

/**
 * Fetch the workshop catalogue with discovery filters.
 *
 * The hook consumes the paginated `{items, total, limit,
 * offset}` envelope: the first page replaces the list, and
 * `loadMore` appends the next page so the catalogue can be
 * browsed without a full re-fetch. The backend orders pages
 * by `starts_at` ascending, so appended pages continue the
 * existing order and no client-side re-sort is needed.
 *
 * The search term is debounced so typing does not fire a
 * request per keystroke.
 *
 * @param refreshKey - Increment to force a re-fetch (e.g.
 *   after an SSE create/delete event).
 * @param filters - Active discovery filters. Changing any
 *   filter resets the list to the first page.
 */
export function useWorkshopList(refreshKey: number, filters: WorkshopListFilters = {}) {
  const [workshops, setWorkshops] = useState<Workshop[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [debouncedQ, setDebouncedQ] = useState(filters.q ?? "");
  // Monotonic request id so a stale page (e.g. a load-more
  // that was in flight when the filters changed) is
  // discarded instead of appended to the fresh list.
  const requestIdRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQ(filters.q ?? "");
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [filters.q]);

  const fetchPage = useCallback(
    (offset: number) =>
      listWorkshops({
        q: debouncedQ || undefined,
        category: filters.category || undefined,
        state: filters.state ?? "upcoming",
        limit: PAGE_SIZE,
        offset,
      }),
    [debouncedQ, filters.category, filters.state],
  );

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    let active = true;
    setLoading(true);
    fetchPage(0)
      .then((envelope) => {
        if (!active || requestId !== requestIdRef.current) return;
        setWorkshops(envelope.items);
        setTotal(envelope.total);
      })
      .catch((requestError) => {
        if (active && requestId === requestIdRef.current) setError(requestError);
      })
      .finally(() => {
        if (active && requestId === requestIdRef.current) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [fetchPage, refreshKey]);

  const loadMore = useCallback(() => {
    const requestId = ++requestIdRef.current;
    const offset = workshops.length;
    setLoadingMore(true);
    fetchPage(offset)
      .then((envelope) => {
        if (requestId !== requestIdRef.current) return;
        setWorkshops((previous) => [...previous, ...envelope.items]);
        setTotal(envelope.total);
      })
      .catch((requestError) => {
        if (requestId === requestIdRef.current) setError(requestError);
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setLoadingMore(false);
      });
  }, [fetchPage, workshops.length]);

  return {
    workshops,
    total,
    error,
    setError,
    loading,
    loadingMore,
    loadMore,
    hasMore: workshops.length < total,
  };
}
