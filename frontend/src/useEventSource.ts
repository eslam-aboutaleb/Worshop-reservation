/**
 * React hook that opens an `EventSource` against the backend SSE
 * stream and forwards every event to the supplied handler.
 *
 * The hook always connects to `/api/workshops/events`, which is the
 * single global channel exposed by the backend. Each event carries a
 * `workshop_id`; the caller is responsible for filtering.
 *
 * Browser behaviour: `EventSource` auto-reconnects on close. The
 * connection state is not surfaced in this hook; the connection
 * indicator is the UI's responsibility if it wants to show "live"
 * vs. "offline".
 */
import { useEffect, useRef } from "react";

import type { SSEEvent } from "./types";

/**
 * Subscribe to the live SSE stream and invoke `onEvent` per message.
 *
 * The handler is captured in a ref so a new function identity on
 * every render does not cause the `useEffect` to tear down and
 * re-open the connection.
 *
 * @param onEvent - Callback invoked once per parsed event. Malformed
 *   payloads are silently dropped.
 */
export function useEventSource(onEvent: (event: SSEEvent) => void): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const source = new EventSource("/api/workshops/events");
    source.onmessage = (message) => {
      try {
        const parsed = JSON.parse(message.data) as SSEEvent;
        handlerRef.current(parsed);
      } catch {
        // Ignore malformed events; the next valid event will recover.
      }
    };
    return () => {
      source.close();
    };
  }, []);
}
