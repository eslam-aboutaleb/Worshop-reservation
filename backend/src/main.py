"""FastAPI application entry point: the composition root.

This module is deliberately thin. All domain behavior
lives in the ``ws-reservation`` plugin
(``libs/reservation``); all cross-cutting infrastructure
lives in ``ws-core`` (``libs/core``). This module only:

* Resolves the application :class:`Settings` from the
  environment (``configuration/settings.py``).
* Composes the app via :func:`ws_core.app.create_app`,
  mounting :class:`ws_reservation.ReservationPlugin`.

The module is also the CLI entry point: running ``python -m src.main``
starts Uvicorn against the configured host/port. In the Docker image
the same command is invoked by the shell wrapper in ``Dockerfile``
after Alembic has run and the demo data has been seeded.
"""

import structlog
from ws_core.app import create_app
from ws_reservation import ReservationPlugin

from src.configuration.settings import get_settings

logger = structlog.get_logger(__name__)

settings = get_settings()

# The composed app: core infrastructure (logging, database,
# realtime bus, /auth, /health, CORS) plus the reservation
# domain plugin. Kept as a module-level instance for Uvicorn
# (`uvicorn src.main:app`).
app = create_app(settings, plugins=[ReservationPlugin()])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_config=None,
    )
