import { useEffect, useState } from "react";

import type { MyWaitlistEntry } from "@ws/types";

import { getApiClient } from "../../../apiClient";

export function useMyWaitlistEntries() {
  const [entries, setEntries] = useState<MyWaitlistEntry[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getApiClient()
      .waitlist.listMyWaitlistEntries()
      .then(setEntries)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  return { entries, error, setError, loading };
}
