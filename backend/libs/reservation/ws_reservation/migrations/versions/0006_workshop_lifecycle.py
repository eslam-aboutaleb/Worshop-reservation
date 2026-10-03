"""add workshop lifecycle fields

Revision ID: 0006_workshop_lifecycle
Revises: 0005_restrict_reservation_owner

Adds the lifecycle columns the registration-window enforcement
(plan 0.1) depends on:

* ``ends_at`` - nullable; a workshop without an explicit end
  time simply has no "ended" state to compute.
* ``registration_closes_at`` - nullable; the application
  defaults it to ``starts_at`` when absent, so a workshop
  with no explicit deadline closes registration exactly when
  it begins.
* ``status`` - ``draft`` | ``published`` | ``cancelled``.
  Existing rows are backfilled to ``published`` so the current
  catalogue keeps behaving exactly as before the migration.

The status column is a plain ``String`` with a ``CHECK``
constraint rather than a Postgres enum: the values are a small,
stable set and a string column keeps future transitions (and
the partial-index style backstops used elsewhere in this schema)
simple to reason about.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_workshop_lifecycle"
down_revision: str | Sequence[str] | None = "0005_restrict_reservation_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workshops",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workshops",
        sa.Column("registration_closes_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workshops",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="published",
        ),
    )
    op.create_check_constraint(
        "ck_workshops_status",
        "workshops",
        "status IN ('draft', 'published', 'cancelled')",
    )
    # Registration must close before the session starts; a
    # deadline after the start would be a configuration error
    # the application layer also guards against on create/edit.
    op.create_check_constraint(
        "ck_workshops_registration_window",
        "workshops",
        "registration_closes_at IS NULL OR registration_closes_at <= starts_at",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workshops_registration_window", "workshops", type_="check")
    op.drop_constraint("ck_workshops_status", "workshops", type_="check")
    op.drop_column("workshops", "status")
    op.drop_column("workshops", "registration_closes_at")
    op.drop_column("workshops", "ends_at")
