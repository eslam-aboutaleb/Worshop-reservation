# @ws/realtime

Live updates from the workshop-reservation SSE channel.

## Framework-agnostic core

```ts
import { createEventStream } from "@ws/realtime";

const stream = createEventStream("/api/workshops/events");
const sub = stream.subscribe((event) => {
  console.log(event.workshop_id, event.type);
});
sub.unsubscribe(); // or stream.unsubscribe(handler)
stream.close(); // drop all handlers + close the connection
```

`createEventStream(url, deps?)` accepts an injected
`EventSourceCtor` for tests and non-browser runtimes.
Malformed payloads are silently dropped; the next valid
event recovers the stream.

## React binding

```ts
import { useEventStream } from "@ws/realtime/react";

useEventStream((event) => {
  // filter on event.workshop_id / event.type
});
```

`useEventStream(onEvent, url?)` preserves the
handler-in-ref pattern: the handler is captured in a ref
and updated every render, so a new function identity does
not tear down the `EventSource` connection. The `url`
defaults to the global `/api/workshops/events` channel.

## Contract notes

- The stream is unauthenticated and carries aggregate
  counts only — no reservation ids (an id is a
  cancellation capability; broadcasting one would hand
  every visitor a list of bookings to attack).
- `EventSource` auto-reconnects on close; connection
  state is intentionally not surfaced (a "live" indicator
  is the UI's responsibility).
- Event payload shapes are typed by `SSEEvent` in
  `@ws/types` (hand-maintained — SSE is not part of the
  OpenAPI document).
