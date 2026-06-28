"""create generation history table"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("voice_id", sa.String(length=128), nullable=False),
        sa.Column("voice_name", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=24), nullable=True),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("format", sa.String(length=12), nullable=False),
        sa.Column("instruct_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=True),
        sa.Column("audio_path", sa.String(length=1024), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_history_model_id", "generation_history", ["model_id"])
    op.create_index("ix_generation_history_status", "generation_history", ["status"])
    op.create_index("ix_generation_history_cache_key", "generation_history", ["cache_key"])
    op.create_index("ix_generation_history_created_at", "generation_history", ["created_at"])


def downgrade() -> None:
    op.drop_table("generation_history")
