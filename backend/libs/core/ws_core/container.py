"""Dependency container handed to plugins at registration time.

The composition root (:func:`ws_core.app.create_app`) builds a
:class:`Container` from the resolved settings and passes it to
every plugin's ``register(app, container)``. A plugin reads what
it needs - the event bus, the realtime bus, the session factory -
and never reaches into a process-wide singleton: the container is
the single seam between the core, the app, and the plugins.

Why a Protocol?
---------------

Plugins depend on the :class:`Container` *shape*, not on the
concrete :class:`DefaultContainer` the composition root builds.
Any object exposing the same attributes satisfies the protocol,
so a test (or an alternative composition root) can hand plugins
its own container without the plugins knowing.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ws_core.config import CoreSettings
from ws_core.events import EventBus
from ws_core.rate_limit import RateLimiter
from ws_core.realtime import RealtimeBus


@runtime_checkable
class Container(Protocol):
    """The dependency surface plugins may read.

    Attributes:
        settings: The resolved application settings.
        event_bus: The domain event bus services publish on.
        realtime_bus: The SSE delivery bus (also installed as
            the process-wide default by the composition root).
        session_factory: The process-wide async session factory
            (built by ``ws_core.db.init_db``).
        rate_limiter: The auth rate limiter.
    """

    settings: CoreSettings
    event_bus: EventBus
    realtime_bus: RealtimeBus
    session_factory: async_sessionmaker[AsyncSession]
    rate_limiter: RateLimiter


@dataclass
class DefaultContainer:
    """The :class:`Container` built by :func:`ws_core.app.create_app`.

    Attributes:
        settings: The resolved application settings.
        event_bus: The domain event bus services publish on.
        realtime_bus: The SSE delivery bus.
        session_factory: The process-wide async session factory.
        rate_limiter: The auth rate limiter.
    """

    settings: CoreSettings
    event_bus: EventBus
    realtime_bus: RealtimeBus
    session_factory: async_sessionmaker[AsyncSession]
    rate_limiter: RateLimiter
