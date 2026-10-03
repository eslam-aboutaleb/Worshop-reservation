"""In-process :class:`~ws_core.events.EventBus`.

Delivers events to handlers sequentially on the calling
event loop. Sequential delivery is deliberate: the SSE
projector relies on publication order (a cancellation
must not overtake the creation it follows).

Handler failures are logged and swallowed so a broken
consumer cannot fail a domain mutation that has already
committed - the bus is a notification channel, not part
of the transaction.
"""

import structlog

from ws_core.events import DomainEvent, EventBus, EventHandler

logger = structlog.get_logger(__name__)


class InProcessEventBus(EventBus):
    """Single-process event bus with sequential delivery."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Append ``handler`` to the delivery list.

        Args:
            handler: Async callable invoked on each publish.
        """
        self._handlers.append(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Deliver ``event`` to every handler, in order.

        The handler list is snapshotted so a handler that
        subscribes during delivery does not mutate the
        iteration. Each handler's exception is logged and
        swallowed; delivery continues with the next
        handler.

        Args:
            event: The event to deliver.
        """
        for handler in list(self._handlers):
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "eventbus.handler_failed",
                    event_type=event.type,
                )
