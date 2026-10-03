"""add idempotency key expiry

Revision ID: 0007_idempotency_expiry
Revises: 0006_workshop_lifecycle

The ``idempotency_keys`` table grew unboundedly (plan 0.4):
every successful reservation added a row and nothing ever
removed them. This migration adds an ``expires_at`` column
(defaulting to now + 7 days for existing rows) and the
replay query filters on it, so old keys stop replaying and
the startup sweep can delete them.

Seven days is long enough to cover a legitimate client retry
storm (a flaky network, a user double-clicking) while short
enough that the table stays bounded in practice.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_idempotency_expiry"
down_revision: str | Sequence[str] | None = "0006_workshop_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '7 days'"),
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_column("idempotency_keys", "expires_at")
