"""Play statuses: want to play, playing, completed, paused, dropped.

Every existing row and ID is kept; this only adds one table. A player has at
most one status per game. Nothing is backfilled: statuses are set by hand,
never guessed from playtime or achievements.

This migration does not import application code.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_game_statuses"
down_revision = "0010_social"
branch_labels = None
depends_on = None

PLAY_STATUSES = ("want_to_play", "playing", "completed", "paused", "dropped")


def _one_of(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade():
    op.create_table(
        "game_statuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "game_id", name="uq_game_statuses_user_game"),
        sa.CheckConstraint(_one_of("status", PLAY_STATUSES), name="ck_game_statuses_status"),
    )
    op.create_index("ix_game_statuses_game_id", "game_statuses", ["game_id"])


def downgrade():
    op.drop_index("ix_game_statuses_game_id", table_name="game_statuses")
    op.drop_table("game_statuses")
