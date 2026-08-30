"""Runtime configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
