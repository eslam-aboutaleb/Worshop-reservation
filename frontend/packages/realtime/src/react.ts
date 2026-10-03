/**
 * React binding for `@ws/realtime`.
 *
 * `useEventStream` opens an `EventSource` against the
 * backend SSE stream and forwards every event to the
 * supplied handler. It always connects to the global
 * `/api/workshops/events` channel unless a different
 * `url` is supplied; each event carries a
 * `workshop_id` and the caller is responsible for
 * filtering.
 */
import { useEffect, useRef } from "react";

import type { SSEEvent } from "@ws/types";

import { createEventStream } from "./index";

/**
 * Subscribe to the live SSE stream and invoke `onEvent`
 * per message.
 *
 * The handler is captured in a ref so a new function
 * identity on every render does not cause the
 * `useEffect` to tear down and re-open the connection.
 *
 * @param onEvent - Callback invoked once per parsed
 *   event. Malformed payloads are silently dropped.
 * @param url - SSE endpoint. Defaults to the global
 *   workshop events channel.
 */
export function useEventStream(
  onEvent: (event: SSEEvent) => void,
  url = "/api/workshops/events",
): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const stream = createEventStream(url);
    const subscription = stream.subscribe((event) => {
      handlerRef.current(event);
    });
    return () => {
      subscription.unsubscribe();
      stream.close();
    };
  }, [url]);
}
