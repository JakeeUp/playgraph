"""Index the newest-snapshot-per-game lookup.

Snapshots are append-only, so one user's rows grow by a library's worth every
sync, and the library, genre graph, feed and sync all ask for the newest row
per game. Without an index each of those scans the whole table and sorts. With
(user_id, game_id, captured_at, id) the database reads only that user's rows,
already grouped by game. Index only: no rows change, so downgrade is safe.
"""
from alembic import op

revision = "0006_snapshot_lookup_index"
down_revision = "0005_one_link_per_platform"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_playtime_snapshots_user_game_captured", "playtime_snapshots",
                    ["user_id", "game_id", "captured_at", "id"])
    op.create_index("ix_comments_review_id", "comments", ["review_id"])
    op.create_index("ix_reviews_game_id", "reviews", ["game_id"])


def downgrade():
    op.drop_index("ix_reviews_game_id", table_name="reviews")
    op.drop_index("ix_comments_review_id", table_name="comments")
    op.drop_index("ix_playtime_snapshots_user_game_captured", table_name="playtime_snapshots")
