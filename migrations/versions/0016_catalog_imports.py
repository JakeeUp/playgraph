"""Private audit fields for player-added IGDB games."""
from alembic import op
import sqlalchemy as sa

revision = "0016_catalog_imports"
down_revision = "0015_profile_images"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("added_by_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("added_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_games_added_by_user_id", "users", ["added_by_user_id"], ["id"])


def downgrade():
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint("fk_games_added_by_user_id", type_="foreignkey")
        batch.drop_column("added_at")
        batch.drop_column("added_by_user_id")
