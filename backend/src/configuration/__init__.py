"""Application configuration: settings, database engine, and FastAPI dependencies.

Everything in this subpackage is loaded once at import time and shared
across the app via module-level singletons (``settings``, ``engine``,
``async_session_factory``). Tests that need isolation can either patch
``get_settings`` or build their own engine; see ``tests/conftest.py``.
"""
