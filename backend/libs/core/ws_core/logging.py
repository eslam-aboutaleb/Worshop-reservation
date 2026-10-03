"""Structured logging configuration for the service.

All stdlib loggers (uvicorn, sqlalchemy, asyncpg, …) flow through
the same structlog-rendered formatter so every line carries
``timestamp``, ``level``, ``logger``, and any bound contextvars
(e.g. ``request_id``). Format is JSON in production and a colored
console renderer in development, controlled by
``Settings.log_format``.

The per-logger entries pin uvicorn/sqlalchemy/asyncpg at sensible
defaults: turning the app to ``DEBUG`` should not also enable
SQL statement logging. Set ``LOG_LEVEL_SQLALCHEMY=DEBUG`` (future
knob) to override; for now the hard pin keeps the noise out.
"""

import logging
import logging.config

import structlog

from ws_core.config import get_settings


def configure_logging() -> None:
    """Configure structlog and the stdlib ``logging`` tree.

    Reads the registered settings (see
    :func:`ws_core.config.configure`) for the log level and
    renderer format.
    """
    settings = get_settings()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    final_processor: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        final_processor,
                    ],
                    "foreign_pre_chain": shared_processors,
                },
            },
            "handlers": {
                "default": {
                    "level": settings.log_level,
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "loggers": {
                "": {
                    "handlers": ["default"],
                    "level": settings.log_level,
                },
                "uvicorn": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "sqlalchemy.engine": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "asyncpg": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )
