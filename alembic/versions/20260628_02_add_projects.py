"""add projects table and generation_history.project_id"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_02"
down_revision = "20260628_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_created_at", "projects", ["created_at"])
    op.add_column(
        "generation_history",
        sa.Column("project_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_generation_history_project_id", "generation_history", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_generation_history_project_id", "generation_history")
    op.drop_column("generation_history", "project_id")
    op.drop_index("ix_projects_created_at", "projects")
    op.drop_table("projects")
