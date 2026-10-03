"""add organizations, memberships, and user roles

Revision ID: 0011_organizations
Revises: 0010_booking_code

Implements the roles & organizations foundation (roadmap
2.1, plan 05):

* ``users.role`` - ``attendee`` | ``organizer`` |
  ``admin``. Existing rows are backfilled to
  ``attendee`` via the column's ``server_default``;
  the configured super-admin keeps its ``is_admin``
  flag, which remains the super-admin signal and is
  deliberately not migrated.
* ``organizations`` - an organizing account's container.
  ``slug`` is unique so organizations can be addressed
  by a stable, URL-safe identifier.
* ``organization_memberships`` - the link between users
  and organizations. The primary key is the
  ``(user_id, organization_id)`` pair, which is also
  the uniqueness constraint the plan calls for; a user
  holds at most one role per organization. Both foreign
  keys cascade so deleting either side removes the
  membership.
* ``workshops.organization_id`` - nullable ownership
  link. ``ON DELETE SET NULL`` keeps a workshop
  bookable when its organization is deleted; an
  unattached workshop stays admin-managed.

As elsewhere in the schema, roles are plain ``String``
columns with ``CHECK`` constraints rather than Postgres
enums: the value sets are small and stable, and string
columns keep future transitions simple to reason about.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_organizations"
down_revision: str | Sequence[str] | None = "0010_booking_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Platform role on the user account. The server
    # default backfills every existing row to "attendee".
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), nullable=False, server_default="attendee"),
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('attendee', 'organizer', 'admin')",
    )

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.create_table(
        "organization_memberships",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'member')",
            name="ck_organization_memberships_role",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_organization_memberships_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_memberships_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "organization_id",
            name="pk_organization_memberships",
        ),
    )

    op.add_column(
        "workshops",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_workshops_organization_id",
        "workshops",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workshops_organization_id", "workshops", type_="foreignkey")
    op.drop_column("workshops", "organization_id")
    op.drop_table("organization_memberships")
    op.drop_table("organizations")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
