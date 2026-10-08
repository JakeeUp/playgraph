"""Wide hero art from IGDB.

Adds games.hero_image_id: an IGDB artwork (or, failing that, screenshot)
image ID used for the banner behind a game's page and the library heading.
Steam games keep using Steam's own hero art; this gives every other game the
same treatment. NULL until the next IGDB refresh. Additive only.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_igdb_hero_art"
down_revision = "0008_igdb_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("games", sa.Column("hero_image_id", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("games") as batch:
        batch.drop_column("hero_image_id")
