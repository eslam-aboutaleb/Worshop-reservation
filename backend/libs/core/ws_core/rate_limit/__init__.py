"""Authentication rate limiting.

A small ``RateLimiter`` interface sits between the auth
routers and the attempt counter so a Redis fixed window
(``INCR`` + ``EXPIRE``) can replace the in-process
implementation without touching any router.

The default implementation is an **in-process fixed
window** keyed by ``(client IP, email)``. It is
deliberately simple: it protects a single-process
deployment against credential brute-force and signup
spam, and it is the documented limitation that a Redis
backend removes (a multi-replica deployment would need
the shared store to see attempts across replicas).

Semantics
---------

* ``check(key)`` reports whether ``key`` may attempt.
  It is called **before** credential verification so a
  locked-out caller never reaches the password check.
* ``record_failure(key)`` is called when verification
  fails (unknown email, wrong password, duplicate signup).
* ``reset(key)`` is called on success so a legitimate
  user never accumulates a lockout.

After ``max_attempts`` failures inside the rolling
``window_seconds`` window, ``check`` returns ``False``
until the oldest failures age out - a lockout of roughly
``window_seconds``.
"""

import threading
import time
from abc import ABC, abstractmethod

from ws_core.config import get_settings


class RateLimiter(ABC):
    """Interface for attempt-budget enforcement on auth endpoints.

    Implementations must be safe under concurrent access.
    The in-process default uses a ``threading.Lock``; a Redis
    implementation relies on the atomicity of ``INCR``.
    """

    @abstractmethod
    async def check(self, key: str) -> bool:
        """Return True if ``key`` is within its attempt budget.

        Args:
            key: The rate-limit key (``"<ip>:<email>"``).

        Returns:
            ``True`` when the caller may attempt; ``False``
            when the key is locked out.
        """

    @abstractmethod
    async def record_failure(self, key: str) -> None:
        """Record a failed attempt for ``key``.

        Args:
            key: The rate-limit key.
        """

    @abstractmethod
    async def reset(self, key: str) -> None:
        """Clear ``key``'s failure budget (e.g. on success).

        Args:
            key: The rate-limit key.
        """


class InMemoryRateLimiter(RateLimiter):
    """Fixed-window attempt counter backed by process memory.

    Attributes:
        _max_attempts: Failures allowed inside the window
            before the key is locked out.
        _window_seconds: How long a failure counts toward
            the budget. The lockout lasts roughly this long
            after the last burst of failures.
        _failures: ``key`` → monotonic timestamps of failed
            attempts. Pruned on every access.
        _lock: Serializes access to ``_failures``. A
            ``threading.Lock`` (not an ``asyncio.Lock``)
            because the guard is a process-wide singleton
            shared across event loops: pytest-asyncio runs
            function-scoped loops, and an ``asyncio.Lock``
            binds to the loop of its first user, raising a
            cross-loop ``RuntimeError`` for every later one.
            The critical sections are purely synchronous, so
            a threading lock is semantically identical here.
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    async def check(self, key: str) -> bool:
        with self._lock:
            return self._count(key) < self._max_attempts

    async def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key)
            self._failures.setdefault(key, []).append(time.monotonic())

    async def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _count(self, key: str) -> int:
        """Return the number of in-window failures for ``key``."""
        self._prune(key)
        return len(self._failures.get(key, []))

    def _prune(self, key: str) -> None:
        """Drop failures that have aged out of the window."""
        timestamps = self._failures.get(key)
        if not timestamps:
            return
        cutoff = time.monotonic() - self._window_seconds
        fresh = [stamp for stamp in timestamps if stamp > cutoff]
        if len(fresh) != len(timestamps):
            if fresh:
                self._failures[key] = fresh
            else:
                self._failures.pop(key, None)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide rate limiter.

    The limiter is a lazy singleton configured from
    ``Settings.auth_max_attempts`` and
    ``Settings.auth_lockout_seconds``. It is created once
    and shared across requests so the attempt budget
    persists between calls.

    Returns:
        The shared ``RateLimiter`` instance.
    """
    global _limiter
    if _limiter is None:
        settings = get_settings()
        _limiter = InMemoryRateLimiter(
            max_attempts=settings.auth_max_attempts,
            window_seconds=settings.auth_lockout_seconds,
        )
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the singleton so the next call rebuilds it.

    Test-only helper: lets a test clear accumulated
    attempt budgets between cases.
    """
    global _limiter
    _limiter = None


def auth_rate_limit_key(client_ip: str, email: str) -> str:
    """Build the rate-limit key for an auth attempt.

    The key is the ``(client IP, email)`` pair: a single IP
    hammering many emails, or many IPs hammering one email,
    are tracked separately.

    Args:
        client_ip: The peer address of the request.
        email: The account email being targeted.

    Returns:
        A ``"<ip>:<email>"`` key string.
    """
    return f"{client_ip}:{email}"
