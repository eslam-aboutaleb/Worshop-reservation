import { useCallback, useEffect, useState } from "react";

import { cancelReservation, listMyReservations } from "../../../api";
import type { MyReservation } from "../../../types";

export function useMyReservations() {
  const [reservations, setReservations] = useState<MyReservation[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listMyReservations()
      .then(setReservations)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  const cancel = useCallback(async (reservationId: string) => {
    try {
      await cancelReservation(reservationId);
      setReservations((items) =>
        items.map((item) =>
          item.id === reservationId
            ? { ...item, status: "cancelled", cancelled_at: new Date().toISOString() }
            : item,
        ),
      );
    } catch (requestError) {
      setError(requestError);
    }
  }, []);

  return { reservations, error, setError, loading, cancel };
}
