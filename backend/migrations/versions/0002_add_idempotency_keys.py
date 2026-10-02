"""add idempotency key storage

Revision ID: 0002_add_idempotency_keys
Revises: 0001_initial
Create Date: 2026-08-27 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_add_idempotency_keys"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workshop_id"],
            ["workshops.id"],
            name="fk_idempotency_keys_workshop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name="fk_idempotency_keys_reservation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key", "workshop_id", name="pk_idempotency_keys"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
