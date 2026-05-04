"""backups table + extend tasks.kind CHECK

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="creating"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("s3_key", name="uq_backups_s3_key"),
        sa.CheckConstraint("status IN ('creating','available','failed')", name="ck_backups_status"),
    )
    op.create_index(
        "ix_backups_server_created",
        "backups",
        ["server_id", sa.text("created_at DESC")],
    )

    op.drop_constraint("ck_tasks_kind", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_kind",
        "tasks",
        "kind IN ('provision','start','stop','restart','delete','backup','restore')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_kind", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_kind",
        "tasks",
        "kind IN ('provision','start','stop','restart','delete')",
    )
    op.drop_index("ix_backups_server_created", table_name="backups")
    op.drop_table("backups")
