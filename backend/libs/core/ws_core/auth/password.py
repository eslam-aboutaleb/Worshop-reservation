"""Password hashing with salted Argon2id.

The service deliberately does not depend on an external identity
provider for credential storage: there is one binary, one user
table, and one signing key. That is the right trade-off for a
single-replica dev deployment; swap the issuance layer for OIDC /
Auth0 / Cognito integration before going to production.

``argon2-cffi`` emits a self-describing PHC string that embeds the
algorithm version, cost parameters, salt, and digest.
:func:`verify_password` is the only function that should ever look
at a stored hash; the plaintext password is never logged, never
returned, and never persisted.

The hasher is a module-level singleton: constructing a
``PasswordHasher`` allocates internal Argon2 parameters, so sharing
one instance avoids that overhead on every login. It is also the
source of the cost parameters embedded in new hashes.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

# OWASP's Argon2id baseline: 19 MiB of memory, two iterations, and one lane.
# The PHC string embeds these values, so the stored hash remains self-describing.
_ARGON2_MEMORY_COST_KIB = 19 * 1024
_ARGON2_TIME_COST = 2
_ARGON2_PARALLELISM = 1
_PASSWORD_HASHER = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST_KIB,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A valid hash keeps the unknown-email login path as expensive as
# the known-email path without generating a new hash for every request.
DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("dummy")


def hash_password(password: str) -> str:
    """Hash a plaintext password with salted Argon2id.

    Args:
        password: The plaintext password to hash. Never logged.

    Returns:
        A self-describing Argon2id PHC string containing the random
        salt, digest, and cost parameters.
    """
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Check a plaintext password against a stored Argon2id hash.

    ``argon2-cffi`` performs the algorithm-specific verification and
    comparison. Malformed hashes and mismatches are treated as a
    non-match so a corrupt database row cannot crash login.

    Args:
        password: Plaintext password from the login request.
        encoded: Value from ``users.password_hash``.

    Returns:
        ``True`` if the password matches, ``False`` otherwise.
    """
    try:
        return _PASSWORD_HASHER.verify(encoded, password)
    except (InvalidHashError, VerificationError):
        return False
