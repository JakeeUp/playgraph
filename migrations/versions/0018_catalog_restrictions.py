"""Hide IGDB-restricted games without scanning summaries on every browse."""
from alembic import op
import sqlalchemy as sa

revision = "0018_catalog_restrictions"
down_revision = "0017_catalog_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("games", sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_games_public_catalog", "games", ["content_kind", "excluded", "name", "id"])
    op.create_table("catalog_restriction_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phase", sa.String(), nullable=False, server_default="theme"),
        sa.Column("after_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_table("catalog_restriction_state")
    op.drop_index("ix_games_public_catalog", table_name="games")
    op.drop_column("games", "excluded")
