"""HTTP API surface.

Submodules:

* ``routers/``: grouped by resource (``workshops``, ``reservations``,
  ``users``). See ``routers/__init__.py`` for the composition.
"""

from src.api.routers import router

__all__ = ["router"]
