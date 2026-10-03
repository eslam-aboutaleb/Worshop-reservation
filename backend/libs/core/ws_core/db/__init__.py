"""Async database primitives: the declarative ``Base``, the engine
factory, and the ``get_db`` FastAPI dependency.

The engine and session factory are module-level state on
:mod:`ws_core.db.engine`, populated by :func:`init_db`. They are
deliberately **not** re-exported from this package: ``init_db``
rebinds them, so a by-value re-export here would capture the
pre-initialization ``None`` and go stale. Access them through the
submodule instead (``ws_core.db.engine.async_session_factory``).
"""

from ws_core.db.base import Base
from ws_core.db.engine import get_db, init_db

__all__ = ["Base", "get_db", "init_db"]
