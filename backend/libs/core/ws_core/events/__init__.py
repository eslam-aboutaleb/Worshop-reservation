"""Domain event bus port.

Domain code publishes :class:`DomainEvent` instances on an
:class:`EventBus` and never knows who consumes them. The
reservation plugin (and any future plugin) subscribes its
own handlers - today that is the SSE projector
(``src/realtime_projector.py``), which translates events
into the realtime bus payloads the frontend already
consumes.

Why a port?
-----------

The services previously called ``realtime.publish``
directly, which hard-wired every domain mutation to one
delivery mechanism (SSE). The bus indirection lets a
deployment swap delivery (SSE, websocket, email, audit
log) without touching domain code, and lets tests assert
on emitted events without a live bus.

Handler contract
----------------

Handlers are async callables receiving the event. The
in-process bus delivers sequentially (publication order
is preserved) and **logs and swallows** handler
exceptions: a failing projector must not roll back or
fail the domain mutation that already committed.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(kw_only=True)
class DomainEvent:
    """A domain fact published on the :class:`EventBus`.

    ``type`` is the machine-readable event name (e.g.
    ``"reservation_created"``); ``payload`` carries the
    event data as JSON-serializable primitives. Concrete
    events (see ``src/events.py``) populate both in
    ``__post_init__`` so the defaults below are never
    observed outside this module.

    Attributes:
        type: Machine-readable event name.
        payload: JSON-serializable event data.
        occurred_at: When the event occurred (UTC).
    """

    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


EventHandler = Callable[[DomainEvent], Awaitable[None]]
"""Async callable invoked with a :class:`DomainEvent`."""


class EventBus(ABC):
    """Port for publishing and subscribing to domain events."""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Deliver ``event`` to every subscribed handler.

        Args:
            event: The event to publish.
        """

    @abstractmethod
    def subscribe(self, handler: EventHandler) -> None:
        """Register ``handler`` for every future event.

        Args:
            handler: Async callable invoked on each publish.
        """
