import { useEffect, useState } from "react";

import { listWorkshops } from "../../../api";
import type { Workshop } from "../../../types";

export function useWorkshopList(refreshKey: number) {
  const [workshops, setWorkshops] = useState<Workshop[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    listWorkshops()
      .then((items) => {
        if (!active) return;
        // Sort by start time ascending so the soonest session is
        // always first. The backend already returns this order, but
        // the list is re-sorted here so SSE updates and any future
        // cache layers don't end up shuffling the list.
        const sorted = [...items].sort(
          (a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime(),
        );
        setWorkshops(sorted);
      })
      .catch((requestError) => active && setError(requestError))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [refreshKey]);

  return { workshops, error, setError, loading };
}
