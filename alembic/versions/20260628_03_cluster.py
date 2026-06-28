"""add cluster job columns and cluster_nodes table"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_03"
down_revision = "20260628_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_history", sa.Column("assigned_node", sa.String(length=64), nullable=True))
    op.add_column("generation_history", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("generation_history", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("generation_history", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_generation_history_assigned_node", "generation_history", ["assigned_node"])

    op.create_table(
        "cluster_nodes",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("models", sa.Text(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("node_id"),
    )


def downgrade() -> None:
    op.drop_table("cluster_nodes")
    op.drop_index("ix_generation_history_assigned_node", "generation_history")
    op.drop_column("generation_history", "priority")
    op.drop_column("generation_history", "attempts")
    op.drop_column("generation_history", "lease_expires_at")
    op.drop_column("generation_history", "assigned_node")
