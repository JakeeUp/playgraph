"""Durable public catalog sync cursor and indexed title browsing."""
from alembic import op
import sqlalchemy as sa

revision = "0017_catalog_sync"
down_revision = "0016_catalog_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("catalog_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("after_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_after", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_end", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_games_catalog_name", "games", ["content_kind", "name", "id"])


def downgrade():
    op.drop_index("ix_games_catalog_name", table_name="games")
    op.drop_table("catalog_sync_state")
