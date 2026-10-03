"""Application configuration: runtime settings.

Settings are loaded once at import time and shared across the
app via the ``get_settings`` singleton, and registered with
ws-core (``ws_core.config.configure``) so its domain-agnostic
modules can read them. The database engine and session factory
live in ``ws_core.db.engine``. Tests that need isolation can
either patch ``get_settings`` or build their own engine; see
``tests/conftest.py``.
"""
