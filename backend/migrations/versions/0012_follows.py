"""add organization follows

Revision ID: 0012_follows
Revises: 0011_organizations

Implements organization following (roadmap 2.3). An attendee
can follow an organization to see its workshops in the
"following" filter on the discovery list.

The table is keyed on the ``(user_id, organization_id)``
pair - a user follows an organization at most once, so the
pair *is* the natural key and the primary key satisfies the
plan's ``unique (user_id, organization_id)`` constraint, the
same design as ``organization_memberships``. Both foreign
keys cascade so deleting either side removes the follow.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_follows"
down_revision: str | Sequence[str] | None = "0011_organizations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_follows",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_organization_follows_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_follows_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "organization_id", name="pk_organization_follows"
        ),
    )


def downgrade() -> None:
    op.drop_table("organization_follows")
