"""Realtime bus factory and the process-wide default bus.

The composition root (``src.main.create_app``) builds a
bus from the resolved settings and installs it with
:func:`set_realtime_bus`. Module-level helpers in
:mod:`ws_core.realtime` delegate to the installed bus,
so call sites (the SSE router, tests) never import a
concrete implementation.

Selection rule: a non-empty ``redis_url`` selects the
Redis adapter (multi-replica); anything else selects
the in-process bus.
"""

from ws_core.config import CoreSettings, get_settings
from ws_core.realtime.in_process import InProcessRealtimeBus
from ws_core.realtime.port import RealtimeBus
from ws_core.realtime.redis import RedisRealtimeBus

_default_bus: RealtimeBus | None = None


def create_realtime_bus(settings: CoreSettings) -> RealtimeBus:
    """Build the bus selected by ``settings.redis_url``.

    Args:
        settings: The resolved settings. A non-empty
            ``redis_url`` selects the Redis adapter;
            an empty one (the default) selects the
            in-process bus.

    Returns:
        A new, unstarted :class:`~ws_core.realtime.port.RealtimeBus`.
    """
    if settings.redis_url:
        return RedisRealtimeBus(settings.redis_url)
    return InProcessRealtimeBus()


def set_realtime_bus(bus: RealtimeBus) -> None:
    """Install ``bus`` as the process-wide default.

    Called by the composition root and by tests that
    need an isolated bus instance.

    Args:
        bus: The bus that module-level helpers delegate to.
    """
    global _default_bus
    _default_bus = bus


def get_realtime_bus() -> RealtimeBus:
    """Return the installed default bus.

    Lazily initializes an in-process bus from the
    registered settings when nothing has been
    installed yet, so importing :mod:`ws_core.realtime`
    and publishing without a composition root still
    works (the app always installs one explicitly).

    Returns:
        The default :class:`~ws_core.realtime.port.RealtimeBus`.
    """
    global _default_bus
    if _default_bus is None:
        _default_bus = create_realtime_bus(get_settings())
    return _default_bus
