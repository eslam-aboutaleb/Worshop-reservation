"""ws-core: domain-agnostic backend toolkit.

This package holds every concern that is not specific to the
reservation domain: runtime configuration (the ``Settings``
protocol + registry), the async database engine factory, the
declarative ``Base``, password hashing and JWT auth, the
domain-error envelope and handler registry, the in-process /
Redis realtime buses, the auth rate limiter, and pagination
helpers.

The application (and any domain plugin such as
``ws-reservation``) depends on ``ws-core`` and satisfies its
protocols with its own concrete ``Settings``. Nothing in
``ws-core`` may import a domain plugin or the application.
"""
