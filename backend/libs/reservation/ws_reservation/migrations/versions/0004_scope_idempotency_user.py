"""scope idempotency keys to the owning account

Revision ID: 0004_scope_idempotency_user
Revises: 0003_add_user_accounts

The idempotency key is a client-supplied opaque string, not a
credential. The replay lookup is now scoped to the requesting account
so that reusing somebody else's key cannot return their reservation -
attendee name and email included. That only works if the storage key
carries the account too, so ``user_id`` joins the primary key.

Existing rows are backfilled from the reservation they point at.
Rows whose reservation is unowned (``user_id IS NULL``) cannot be
attributed to an account and are deleted: with strict ownership those
bookings are no longer cancellable by anyone, so keeping a key that
nobody can legitimately replay would only reserve the value forever.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_scope_idempotency_user"
down_revision: str | Sequence[str] | None = "0003_add_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Drop keys that cannot be attributed to an account before the
    # column becomes NOT NULL; see the module docstring.
    op.execute(
        """
        DELETE FROM idempotency_keys k
        WHERE NOT EXISTS (
            SELECT 1 FROM reservations r
            WHERE r.id = k.reservation_id AND r.user_id IS NOT NULL
        )
        """
    )

    op.execute(
        """
        UPDATE idempotency_keys k
        SET user_id = r.user_id
        FROM reservations r
        WHERE r.id = k.reservation_id
        """
    )

    op.alter_column("idempotency_keys", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_idempotency_keys_user_id",
        "idempotency_keys",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Re-key the table so the account participates in uniqueness.
    op.drop_constraint("pk_idempotency_keys", "idempotency_keys", type_="primary")
    op.create_primary_key(
        "pk_idempotency_keys", "idempotency_keys", ["key", "workshop_id", "user_id"]
    )


def downgrade() -> None:
    # Two accounts may share a key on one workshop after the upgrade,
    # so collapsing back to (key, workshop_id) can conflict. Keep the
    # earliest row per key and drop the rest.
    op.execute(
        """
        DELETE FROM idempotency_keys a
        USING idempotency_keys b
        WHERE a.key = b.key
          AND a.workshop_id = b.workshop_id
          AND a.ctid > b.ctid
        """
    )
    op.drop_constraint("fk_idempotency_keys_user_id", "idempotency_keys", type_="foreignkey")
    op.drop_constraint("pk_idempotency_keys", "idempotency_keys", type_="primary")
    op.create_primary_key("pk_idempotency_keys", "idempotency_keys", ["key", "workshop_id"])
    op.drop_column("idempotency_keys", "user_id")
