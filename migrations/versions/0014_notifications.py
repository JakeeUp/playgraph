"""Owner-only notifications. Existing data is unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0014_notifications"
down_revision = "0013_reposts_bookmarks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id", ondelete="CASCADE")),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("comments.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('like', 'repost', 'quote', 'comment', 'follow', 'follow_accepted')",
                           name="ck_notifications_kind"),
        sa.CheckConstraint("recipient_id <> actor_id", name="ck_notifications_not_self"),
        sa.CheckConstraint(
            "(kind IN ('follow', 'follow_accepted') AND review_id IS NULL AND comment_id IS NULL) OR "
            "(kind IN ('like', 'repost', 'quote') AND review_id IS NOT NULL AND comment_id IS NULL) OR "
            "(kind = 'comment' AND review_id IS NOT NULL AND comment_id IS NOT NULL)",
            name="ck_notifications_target",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_notifications_recipient_created", "notifications", ["recipient_id", "created_at", "id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade():
    op.drop_table("notifications")
