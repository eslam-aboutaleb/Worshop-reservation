/**
 * Framework-agnostic SSE event stream for the
 * workshop-reservation live channel.
 *
 * The backend exposes a single global channel at
 * `/api/workshops/events`. Each event carries a
 * `workshop_id`; the caller is responsible for
 * filtering.
 *
 * Browser behaviour: `EventSource` auto-reconnects on
 * close. The connection state is not surfaced by this
 * package; a "live" vs. "offline" indicator is the
 * UI's responsibility.
 *
 * The React binding lives in `@ws/realtime/react`
 * (`useEventStream`); this entry point has no React
 * dependency.
 */
import type { SSEEvent } from "@ws/types";

/** Minimal `EventSource` instance surface the stream depends on. */
export interface EventSourceInstance {
  onmessage: ((message: { data: string }) => void) | null;
  close(): void;
}

/** Minimal `EventSource` constructor surface; injectable for tests. */
export interface EventSourceCtor {
  new (url: string): EventSourceInstance;
}

/** Dependencies for {@link createEventStream}; injectable for tests. */
export interface EventStreamDeps {
  /**
   * `EventSource` constructor. Defaults to the global
   * `EventSource`; inject a stub in tests or a
   * polyfill in non-browser runtimes.
   */
  EventSourceCtor?: EventSourceCtor;
}

/** A subscription handle returned by `subscribe`. */
export interface EventSubscription {
  /** Remove this handler from the stream. */
  unsubscribe(): void;
}

/** The stream returned by {@link createEventStream}. */
export interface EventStream {
  /**
   * Add a handler. Every parsed event is forwarded
   * to every subscribed handler.
   */
  subscribe(handler: (event: SSEEvent) => void): EventSubscription;
  /** Remove a handler previously passed to `subscribe`. */
  unsubscribe(handler: (event: SSEEvent) => void): void;
  /** Drop all handlers and close the underlying connection. */
  close(): void;
}

/**
 * Open an SSE event stream.
 *
 * @param url - The SSE endpoint URL.
 * @param deps - Optional dependency overrides.
 * @returns A stream with `subscribe` / `unsubscribe` /
 *   `close`.
 *
 * @example
 * ```ts
 * const stream = createEventStream("/api/workshops/events");
 * const sub = stream.subscribe((event) => console.log(event));
 * // later
 * sub.unsubscribe();
 * stream.close();
 * ```
 */
export function createEventStream(url: string, deps: EventStreamDeps = {}): EventStream {
  const EventSourceCtor = deps.EventSourceCtor ?? EventSource;
  const source = new EventSourceCtor(url);
  const handlers = new Set<(event: SSEEvent) => void>();

  source.onmessage = (message: { data: string }) => {
    let parsed: SSEEvent;
    try {
      parsed = JSON.parse(message.data) as SSEEvent;
    } catch {
      // Ignore malformed events; the next valid event will recover.
      return;
    }
    for (const handler of handlers) {
      try {
        handler(parsed);
      } catch {
        // One throwing handler must not skip the remaining
        // handlers or escape into the EventSource callback.
      }
    }
  };

  return {
    subscribe(handler: (event: SSEEvent) => void): EventSubscription {
      handlers.add(handler);
      return {
        unsubscribe() {
          handlers.delete(handler);
        },
      };
    },
    unsubscribe(handler: (event: SSEEvent) => void): void {
      handlers.delete(handler);
    },
    close(): void {
      handlers.clear();
      source.close();
    },
  };
}
