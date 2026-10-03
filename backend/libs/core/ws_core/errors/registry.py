"""Registration of domain-error exception handlers on a FastAPI app.

Every :class:`~ws_core.errors.DomainError` subclass is translated
by the shared :func:`~ws_core.errors.domain_error_handler`, so
registering a handler is a matter of listing the error classes
the app (or plugin) raises. The composition root calls this once
per app; a plugin calls it from its ``register()`` so the error
envelope travels with the domain.
"""

from collections.abc import Iterable

from fastapi import FastAPI

from ws_core.errors import DomainError, domain_error_handler


def register_domain_handlers(
    app: FastAPI,
    error_classes: Iterable[type[Exception]],
) -> None:
    """Register the shared domain-error handler for each error class.

    Args:
        app: The FastAPI application (or sub-application) to
            register the handlers on.
        error_classes: The exception classes to handle. Each is
            bound to :func:`~ws_core.errors.domain_error_handler`,
            which renders the uniform
            ``{"error": {"code", "message"}}`` envelope for any
            :class:`~ws_core.errors.DomainError` subclass.
    """
    for error_class in error_classes:
        app.add_exception_handler(error_class, domain_error_handler)


__all__ = ["register_domain_handlers", "DomainError"]
