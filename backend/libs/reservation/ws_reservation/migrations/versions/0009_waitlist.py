"""add waitlist entries

Revision ID: 0009_waitlist
Revises: 0008_workshop_metadata

Implements the FIFO waitlist (plan 1.2). A waitlist entry
is a place in line, promoted to a real reservation inside
the cancelling transaction so promotion is exactly-once.

Indexes:
* ``uq_active_waitlist_entry`` - partial unique on
  ``(workshop_id, user_id) WHERE status='active'`` so a
  user can hold only one place in line per workshop (they
  may re-join after being promoted or after leaving).
* ``ix_waitlist_entries_workshop_created`` - the FIFO
  ordering key used by the promotion query.
* ``ix_waitlist_entries_reservation`` - lets a promoted
  entry be found from its reservation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_waitlist"
down_revision: str | Sequence[str] | None = "0008_workshop_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "waitlist_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'promoted', 'cancelled')",
            name="ck_waitlist_entries_status",
        ),
        sa.ForeignKeyConstraint(
            ["workshop_id"],
            ["workshops.id"],
            name="fk_waitlist_entries_workshop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_waitlist_entries_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name="fk_waitlist_entries_reservation_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_waitlist_entries"),
    )
    op.create_index(
        "uq_active_waitlist_entry",
        "waitlist_entries",
        ["workshop_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_waitlist_entries_workshop_created",
        "waitlist_entries",
        ["workshop_id", "created_at"],
    )
    op.create_index(
        "ix_waitlist_entries_reservation",
        "waitlist_entries",
        ["reservation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_waitlist_entries_reservation",
        table_name="waitlist_entries",
    )
    op.drop_index(
        "ix_waitlist_entries_workshop_created",
        table_name="waitlist_entries",
    )
    op.drop_index("uq_active_waitlist_entry", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
