"""add reviews

Revision ID: 0013_reviews
Revises: 0012_follows

Implements post-workshop reviews and ratings (roadmap 2.4).
Only attendees who held a reservation for a workshop whose
session has ended may review, and each attendee may review a
workshop at most once - enforced by the application check
first (for a friendly 4xx) and by the unique constraint
``uq_reviews_workshop_user`` as the race-free backstop.

``rating`` is a ``SmallInteger`` with a ``CHECK`` constraint
keeping it in 1..5; ``text`` is free-form. As elsewhere in
the schema, small stable value sets are plain columns with
CHECK constraints rather than Postgres enums.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_reviews"
down_revision: str | Sequence[str] | None = "0012_follows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        sa.ForeignKeyConstraint(
            ["workshop_id"],
            ["workshops.id"],
            name="fk_reviews_workshop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_reviews_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.UniqueConstraint(
            "workshop_id", "user_id", name="uq_reviews_workshop_user"
        ),
    )


def downgrade() -> None:
    op.drop_table("reviews")
