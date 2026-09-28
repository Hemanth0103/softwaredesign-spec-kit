"""Persist fail-closed readability and unresolved conflict membership."""

import sqlalchemy as sa

from alembic import op

revision = "0002_eligibility"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_revision",
        sa.Column("readable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("source_revision", sa.Column("conflict_group", sa.Uuid(), nullable=True))
    op.create_index("ix_source_revision_conflict_group", "source_revision", ["conflict_group"])
    op.drop_constraint(op.f("ck_source_revision_status"), "source_revision", type_="check")
    op.create_check_constraint(
        op.f("ck_source_revision_status"),
        "source_revision",
        "status IN ('pending_review','approved','active','unresolved','superseded','retired')",
    )


def downgrade() -> None:
    # Do not silently reactivate conflicts when downgrading: fail closed by retiring them.
    op.execute("UPDATE source_revision SET status='retired' WHERE status='unresolved'")
    op.drop_constraint(op.f("ck_source_revision_status"), "source_revision", type_="check")
    op.create_check_constraint(
        op.f("ck_source_revision_status"),
        "source_revision",
        "status IN ('pending_review','approved','active','superseded','retired')",
    )
    op.drop_index("ix_source_revision_conflict_group", table_name="source_revision")
    op.drop_column("source_revision", "conflict_group")
    op.drop_column("source_revision", "readable")
