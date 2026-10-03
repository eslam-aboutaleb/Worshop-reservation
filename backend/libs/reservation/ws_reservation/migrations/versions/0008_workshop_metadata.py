"""add workshop metadata columns

Revision ID: 0008_workshop_metadata
Revises: 0007_idempotency_expiry

Adds the discovery metadata the plan's Phase 1.1 calls
for: ``description`` (long-form), ``category`` (filterable
label, indexed for the category-chip filter) and
``location``. All three default to empty strings so the
existing catalogue rows remain valid and the seed can
populate them going forward.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_workshop_metadata"
down_revision: str | Sequence[str] | None = "0007_idempotency_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workshops",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "workshops",
        sa.Column("category", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column(
        "workshops",
        sa.Column("location", sa.String(length=200), nullable=False, server_default=""),
    )
    op.create_index("ix_workshops_category", "workshops", ["category"])


def downgrade() -> None:
    op.drop_index("ix_workshops_category", table_name="workshops")
    op.drop_column("workshops", "location")
    op.drop_column("workshops", "category")
    op.drop_column("workshops", "description")
