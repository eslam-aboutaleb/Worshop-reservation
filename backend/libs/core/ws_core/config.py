"""Runtime configuration registry for ws-core.

ws-core is domain-agnostic: it must not import the
application's concrete ``Settings``. Instead it declares a
:class:`CoreSettings` :class:`typing.Protocol` covering every
field its modules read, and the application registers its own
concrete settings object with :func:`configure` at import time
(the app's ``src/configuration/settings.py`` calls
``ws_core.config.configure(get_settings())`` at module bottom).

Why a registry rather than a direct import?
--------------------------------------------

A direct ``from src.configuration.settings import get_settings``
would make core depend on the application - the exact coupling
the extraction removes. The registry inverts the dependency:
core defines the *shape* it needs, the application supplies the
value. Any project that satisfies the protocol can mount
ws-core (and plugins like ws-reservation) without copying code.

The environment-variable contract is unchanged: the app's flat
``Settings`` still reads the same env vars; core simply reads
them through the registered instance.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoreSettings(Protocol):
    """The settings surface ws-core modules read.

    Any object with these attributes satisfies the protocol -
    the application's pydantic ``Settings`` does. The
    annotations are for static type checking; the runtime check
    (``runtime_checkable``) only verifies attribute presence.
    """

    # Database (ws_core.db.engine)
    database_url: str
    database_pool_size: int
    database_max_overflow: int
    database_pool_pre_ping: bool
    database_echo: bool

    # Logging (ws_core.logging)
    log_level: str
    log_format: str

    # Auth (ws_core.auth)
    auth_secret_key: str
    admin_email: str | None
    access_token_expire_days: int

    # Auth rate limiting (ws_core.rate_limit)
    auth_max_attempts: int
    auth_lockout_seconds: int

    # Cookie delivery (ws_core.auth.tokens)
    cookie_name: str
    cookie_secure: bool
    cookie_samesite: str

    # Realtime (ws_core.realtime; the Redis adapter reads
    # ``redis_url`` - empty string means "in-process only").
    redis_url: str

    # Server / CORS / SSE (used by the composition root and
    # the SSE heartbeat; declared here so a single protocol
    # covers the whole core surface).
    server_host: str
    server_port: int
    cors_origins: list[str]
    sse_heartbeat_seconds: float


_settings: CoreSettings | None = None


def configure(settings: CoreSettings) -> None:
    """Register the application's concrete settings with core.

    Called once at import time by the application's settings
    module. Idempotent: a later call replaces the earlier
    registration (tests that rebuild settings can re-register).

    Args:
        settings: An object satisfying :class:`CoreSettings`.
    """
    global _settings
    _settings = settings


def get_settings() -> CoreSettings:
    """Return the registered settings instance.

    Returns:
        The ``CoreSettings`` instance registered by
        :func:`configure`.

    Raises:
        RuntimeError: If ``configure`` has not been called yet.
            The application registers its settings at import
            time, so this only fires when core is imported
            without a composition root.
    """
    if _settings is None:
        raise RuntimeError(
            "ws_core.configure(settings) must be called before "
            "ws_core.get_settings() - the application registers "
            "its settings at import time."
        )
    return _settings
