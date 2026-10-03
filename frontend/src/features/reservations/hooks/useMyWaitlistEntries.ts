import { useEffect, useState } from "react";

import { listMyWaitlistEntries } from "../../../api";
import type { MyWaitlistEntry } from "../../../types";

export function useMyWaitlistEntries() {
  const [entries, setEntries] = useState<MyWaitlistEntry[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listMyWaitlistEntries()
      .then(setEntries)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  return { entries, error, setError, loading };
}
