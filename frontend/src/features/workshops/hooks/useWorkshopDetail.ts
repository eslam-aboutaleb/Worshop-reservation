import { useCallback, useEffect, useState } from "react";

import { ApiError, cancelReservation, getWorkshop } from "../../../api";
import type { SSEEvent, WorkshopDetail } from "../../../types";

export function useWorkshopDetail(
  workshopId: string,
  liveEvent: SSEEvent | null,
  userId: string | undefined,
  onSpotsChanged: (workshopId: string, spots: number) => void,
  onDeleted: () => void,
) {
  const [detail, setDetail] = useState<WorkshopDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyReservationId, setBusyReservationId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const fresh = await getWorkshop(workshopId);
      setDetail(fresh);
      onSpotsChanged(workshopId, fresh.available_spots);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.code === "workshop_not_found") {
        onDeleted();
        return;
      }
      setError(requestError);
    }
  }, [onDeleted, onSpotsChanged, workshopId]);

  useEffect(() => {
    void refresh();
  }, [refresh, userId]);

  useEffect(() => {
    if (liveEvent?.workshop_id === workshopId) void refresh();
    // `refresh` is memoized on [workshopId, onDeleted, onSpotsChanged], so
    // a liveEvent for a different workshop still re-runs this effect but
    // calls the same stable `refresh` for this workshop. Adding any
    // non-stable dep to `refresh` will change that contract.
  }, [liveEvent, refresh, workshopId]);

  const cancel = useCallback(
    async (reservationId: string) => {
      setBusyReservationId(reservationId);
      try {
        await cancelReservation(reservationId);
        await refresh();
      } catch (requestError) {
        setError(requestError);
      } finally {
        setBusyReservationId(null);
      }
    },
    [refresh],
  );

  return { detail, error, setError, busyReservationId, refresh, cancel };
}
