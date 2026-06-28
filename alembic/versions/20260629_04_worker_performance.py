"""store worker identity and inference time for performance metrics

Revision ID: 20260629_04
Revises: 20260628_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260629_04"
down_revision = "20260628_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_history", sa.Column("worker_id", sa.String(128), nullable=True))
    op.add_column("generation_history", sa.Column("inference_seconds", sa.Float(), nullable=True))
    op.create_index("ix_generation_history_worker_id", "generation_history", ["worker_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_history_worker_id", table_name="generation_history")
    op.drop_column("generation_history", "inference_seconds")
    op.drop_column("generation_history", "worker_id")
