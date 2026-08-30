"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-26 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workshops",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_capacity", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("max_capacity > 0", name="ck_workshops_positive_capacity"),
        sa.PrimaryKeyConstraint("id", name="pk_workshops"),
    )

    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attendee_name", sa.String(length=200), nullable=False),
        sa.Column("attendee_email", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled')",
            name="ck_reservations_status",
        ),
        sa.ForeignKeyConstraint(
            ["workshop_id"],
            ["workshops.id"],
            name="fk_reservations_workshop_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reservations"),
    )
    op.create_index(
        "uq_active_reservation",
        "reservations",
        ["workshop_id", "attendee_email"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_reservation", table_name="reservations")
    op.drop_table("reservations")
    op.drop_table("workshops")
