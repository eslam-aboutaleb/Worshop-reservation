"""add reservation booking codes

Revision ID: 0010_booking_code
Revises: 0009_waitlist

Adds the server-generated ``booking_code`` (plan 1.4)
that identifies a reservation for the confirmation
ticket and, later, check-in. The code is
``WKS-`` plus six characters from an unambiguous
alphabet (no ``0``/``O``/``1``/``I``/``L``) so it
survives being read aloud or scanned.

Existing rows are backfilled with unique codes in
Python (collision-checked against the rows already
processed in this migration) before the column is
made ``NOT NULL`` and unique.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_booking_code"
down_revision: str | Sequence[str] | None = "0009_waitlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Unambiguous alphabet: no 0/O, 1/I/L confusables.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6


def _generate_code(existing: set[str]) -> str:
    """Return a fresh ``WKS-XXXXXX`` code not in ``existing``."""
    import random

    while True:
        code = "WKS-" + "".join(random.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
        if code not in existing:
            existing.add(code)
            return code


def upgrade() -> None:
    op.add_column(
        "reservations",
        sa.Column("booking_code", sa.String(length=12), nullable=True),
    )
    # Backfill existing rows with unique codes. The column is
    # nullable during the backfill so a mid-migration failure
    # leaves the table consistent.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM reservations")).fetchall()
    used: set[str] = set()
    for (reservation_id,) in rows:
        code = _generate_code(used)
        bind.execute(
            sa.text("UPDATE reservations SET booking_code = :code WHERE id = :id"),
            {"code": code, "id": reservation_id},
        )
    op.alter_column(
        "reservations", "booking_code", existing_type=sa.String(length=12), nullable=False
    )
    op.create_index(
        "uq_reservations_booking_code",
        "reservations",
        ["booking_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_reservations_booking_code", table_name="reservations")
    op.drop_column("reservations", "booking_code")
