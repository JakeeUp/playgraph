"""Keep deleted review IDs retired so shared links cannot change owners."""
from alembic import op

revision = "0002_review_ids"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL already allocates these IDs with a non-recycling sequence.
    # SQLite needs AUTOINCREMENT, requiring a copy that preserves every row/ID.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("reviews", recreate="always",
                                  table_kwargs={"sqlite_autoincrement": True}):
            pass


def downgrade():
    raise RuntimeError("Retired review IDs must not be reused. Restore a verified backup instead.")
