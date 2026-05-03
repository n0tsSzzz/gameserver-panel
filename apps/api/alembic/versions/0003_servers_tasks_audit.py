"""servers + tasks + audit_log; nodes.api_key_hash -> api_key

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # nodes: hash -> plaintext
    op.add_column(
        "nodes",
        sa.Column("api_key", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("nodes", "api_key", server_default=None)
    op.drop_column("nodes", "api_key_hash")

    op.create_table(
        "servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("container_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("host", sa.String(), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column(
            "env_overrides",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "resources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["game_templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("owner_id", "name", name="uq_servers_owner_name"),
        sa.CheckConstraint(
            "status IN ('pending','provisioning','running','stopped','failed','deleting')",
            name="ck_servers_status",
        ),
    )
    op.create_index("ix_servers_node_status", "servers", ["node_id", "status"])
    op.create_index(
        "ix_servers_owner_created",
        "servers",
        ["owner_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('provision','start','stop','restart','delete')",
            name="ck_tasks_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed')",
            name="ck_tasks_status",
        ),
    )
    op.create_index("ix_tasks_server_created", "tasks", ["server_id", sa.text("created_at DESC")])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column(
            "meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_audit_target",
        "audit_log",
        ["target_type", "target_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_target", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_tasks_server_created", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_servers_owner_created", table_name="servers")
    op.drop_index("ix_servers_node_status", table_name="servers")
    op.drop_table("servers")

    op.add_column(
        "nodes",
        sa.Column("api_key_hash", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("nodes", "api_key_hash", server_default=None)
    op.drop_column("nodes", "api_key")
