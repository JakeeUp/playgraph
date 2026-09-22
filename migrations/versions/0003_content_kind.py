"""Store each game's content kind instead of deriving it in every query.

The catalog and feed filtered on a SQL expression that ran string functions
over every joined row, which made those queries roughly 80 times slower. The
column is backfilled with a frozen copy of the classifier: migrations never
import evolving application code, so a later rule change needs its own data
migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_content_kind"
down_revision = "0002_review_ids"
branch_labels = None
depends_on = None

SOFTWARE_APP_IDS = frozenset({1812620, 400040, 431960})
SOFTWARE_GENRES = frozenset({
    "utilities", "animation&modeling", "design&illustration", "photoediting",
    "audioproduction", "videoproduction", "webpublishing", "softwaretraining",
    "education", "accounting", "gamedevelopment",
})


def classify(appid, genres):
    tokens = {token.lower().translate(str.maketrans("", "", " \t\r\n")) for token in (genres or "").split(",")}
    return "software" if appid in SOFTWARE_APP_IDS or tokens & SOFTWARE_GENRES else "game"


def upgrade():
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("content_kind", sa.String(), nullable=False, server_default="game"))
        batch.create_index("ix_games_content_kind", ["content_kind"])
    games = sa.table("games", sa.column("id", sa.Integer), sa.column("steam_appid", sa.Integer),
                     sa.column("genres", sa.String), sa.column("content_kind", sa.String))
    bind = op.get_bind()
    software = [row.id for row in bind.execute(sa.select(games.c.id, games.c.steam_appid, games.c.genres))
                if classify(row.steam_appid, row.genres) == "software"]
    for start in range(0, len(software), 500):
        bind.execute(games.update().where(games.c.id.in_(software[start:start + 500]))
                     .values(content_kind="software"))


def downgrade():
    with op.batch_alter_table("games") as batch:
        batch.drop_index("ix_games_content_kind")
        batch.drop_column("content_kind")
