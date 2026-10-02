"""prevent account deletion from orphaning reservations

Revision ID: 0005_restrict_reservation_owner
Revises: 0004_scope_idempotency_user

Reservation ownership is enforced strictly: a cancel succeeds only
when ``reservations.user_id`` matches the caller. The column was
declared ``ON DELETE SET NULL``, however, so deleting an account would
silently rewrite its active reservations to ``user_id IS NULL`` and
recreate the exact unowned state that made the old authorization bug
reachable - those rows could then be claimed by nobody, and the seat
could never be released.

No user-deletion path exists in the API today, so this is a latent
trap rather than a live exploit. ``RESTRICT`` makes it impossible: an
account that still holds reservations cannot be removed until they are
cancelled or reassigned. Any future account-deletion feature has to
make that choice explicitly instead of inheriting it from the schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_restrict_reservation_owner"
down_revision: str | Sequence[str] | None = "0004_scope_idempotency_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_reservations_user_id", "reservations", type_="foreignkey")
    op.create_foreign_key(
        "fk_reservations_user_id",
        "reservations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_reservations_user_id", "reservations", type_="foreignkey")
    op.create_foreign_key(
        "fk_reservations_user_id",
        "reservations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
