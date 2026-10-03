"""Runtime configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from ws_core.config import configure as configure_ws_core


class Settings(BaseSettings):
    """Settings that vary between deployments or protect the service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(..., description="Async PostgreSQL connection string")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost"]
    )
    auth_secret_key: str = Field(..., min_length=32)
    admin_email: EmailStr | None = None
    access_token_expire_days: int = Field(default=30, ge=1, le=365)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_pre_ping: bool = True
    database_echo: bool = False
    server_host: str = "0.0.0.0"
    server_port: int = Field(default=8000, ge=1, le=65535)
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0, le=300)
    # Auth rate limiting (plan 0.3). A fixed window of
    # ``auth_max_attempts`` failed login/signup attempts per
    # (client IP, email) triggers a ``auth_lockout_seconds``
    # lockout. The counter lives behind the ``RateLimiter``
    # interface so Phase 3 can swap the in-process store for
    # Redis without touching the routers.
    auth_max_attempts: int = Field(default=5, ge=1, le=100)
    auth_lockout_seconds: int = Field(default=900, ge=1, le=86400)
    # Real-time infra (plan 3.1). Empty string means "no Redis
    # configured"; the in-process SSE implementation is used
    # until a URL is provided.
    redis_url: str = ""
    # Transactional email (plan 3.2). ``email_enabled`` gates
    # every send so a deployment without SMTP credentials never
    # blocks a booking flow.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    email_enabled: bool = False
    # Payments (plan 4.2). ``"none"`` is the no-op default; the
    # provider factory keys off this value.
    payment_provider: str = Field(default="none", pattern="^(none|stripe|fawry)$")
    # The bearer token is delivered as an httpOnly cookie. Set to
    # ``true`` in development over plain HTTP; the default ``false``
    # requires HTTPS in production.
    cookie_secure: bool = False
    cookie_samesite: str = Field(default="lax", pattern="^(lax|strict|none)$")
    cookie_name: str = "workshop_access_token"
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: str = Field(default="json", pattern="^(json|console)$")


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance.

    ``functools.lru_cache`` ensures the environment is parsed exactly
    once. Tests that need to mutate settings should clear the cache
    via ``get_settings.cache_clear()`` after monkey-patching the env.

    Returns:
        The configured ``Settings`` object.
    """
    return Settings()


# Register the concrete settings with ws-core. Core modules are
# domain-agnostic: they read configuration through the CoreSettings
# protocol (see ws_core.config), and this module supplies the value.
# The call runs at import time so any later ws_core.config.get_settings()
# resolves to this instance.
configure_ws_core(get_settings())
