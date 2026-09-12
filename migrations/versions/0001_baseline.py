"""Freeze the existing Steam-era schema; never import evolving application models here."""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def schema():
    metadata = sa.MetaData()
    sa.Table("users", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("display_name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    sa.Table("linked_accounts", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform", sa.Enum("steam", name="platform"), nullable=False),
        sa.Column("platform_user_id", sa.String, nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("platform", "platform_user_id"))
    sa.Table("games", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("steam_appid", sa.Integer, unique=True, nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("genres", sa.String), sa.Column("header_image_url", sa.String))
    sa.Table("playtime_snapshots", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("playtime_minutes", sa.Integer),
        sa.Column("achievements_unlocked", sa.Integer), sa.Column("achievements_total", sa.Integer),
        sa.Column("captured_at", sa.DateTime(timezone=True)))
    sa.Table("reviews", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("rating", sa.Float, nullable=False), sa.Column("body", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("verified_playtime_minutes", sa.Integer),
        sa.Column("verified_achievement_pct", sa.Float),
        sa.UniqueConstraint("user_id", "game_id"))
    sa.Table("comments", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("review_id", sa.Integer, sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    return metadata


def upgrade():
    schema().create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("The baseline cannot be dropped. Restore a verified backup instead.")
