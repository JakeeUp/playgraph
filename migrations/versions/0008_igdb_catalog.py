"""IGDB catalog metadata, platforms and the match review list.

Every existing row and ID is kept; this only adds columns and tables.
- games gains summary, first_release_date, cover_image_id (IGDB image ID,
  never a URL) and igdb_refreshed_at. All NULL until an IGDB refresh runs.
  A game's IGDB ID lives in game_external_ids with provider 'igdb', whose
  existing unique (provider, external_id) constraint means one IGDB game maps
  to exactly one PlayGraph game.
- game_platforms lists the platforms a game was released on, by IGDB slug,
  so the catalog can filter by platform.
- igdb_match_candidates holds matches that are not certain enough to apply
  automatically (name-only guesses, or a store ID whose IGDB game already
  belongs to another PlayGraph game). An owner reviews them; nothing here
  merges games.

This migration does not import application code.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_igdb_catalog"
down_revision = "0007_psn_accounts_and_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("games", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("games", sa.Column("first_release_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("games", sa.Column("cover_image_id", sa.String(), nullable=True))
    op.add_column("games", sa.Column("igdb_refreshed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "game_platforms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=True),
        sa.UniqueConstraint("game_id", "slug", name="uq_game_platforms_game_slug"),
    )
    op.create_index("ix_game_platforms_slug", "game_platforms", ["slug"])

    op.create_table(
        "igdb_match_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("igdb_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("game_id", "igdb_id", name="uq_igdb_match_candidates_game_igdb"),
    )
    op.create_index("ix_igdb_match_candidates_status", "igdb_match_candidates", ["status"])


def downgrade():
    op.drop_index("ix_igdb_match_candidates_status", table_name="igdb_match_candidates")
    op.drop_table("igdb_match_candidates")
    op.drop_index("ix_game_platforms_slug", table_name="game_platforms")
    op.drop_table("game_platforms")
    with op.batch_alter_table("games") as batch:
        for name in ("igdb_refreshed_at", "cover_image_id", "first_release_date", "summary"):
            batch.drop_column(name)
