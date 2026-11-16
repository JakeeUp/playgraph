"""Reposts, quote reviews and private bookmarks.

Every existing row and ID is kept.
- reports may now name a repost. SQLite cannot widen a CHECK in place, so
  the reports table is copied with the new target list. Existing rows are
  review, comment or user, which still pass.
- reposts: one plain repost per player and review (quote IS NULL). A quote
  is its own row and can repeat. Quote text is 1 to 280 characters.
- bookmarks: one private save per player and review. No public count.

This migration does not import application code.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_reposts_bookmarks"
down_revision = "0012_handles_likes_favorites"
branch_labels = None
depends_on = None

REPORT_TARGETS = ("review", "comment", "user", "repost")
OLD_TARGETS = ("review", "comment", "user")
REPORT_REASONS = ("spam", "harassment", "hate", "sexual", "spoilers", "other")
REPORT_STATUSES = ("open", "dismissed", "actioned")
REPORT_COLUMNS = (
    "id", "reporter_id", "target_type", "target_id", "reason", "note", "status", "created_at",
)


def _one_of(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _copy_reports(targets, drop_indexes):
    """Rebuild reports so the target CHECK matches `targets`. Rows are kept.

    Index names are global in SQLite, so the old ones have to go before the
    copy creates them again. Downgrade drops them itself before calling this.
    """
    if drop_indexes:
        op.drop_index("uq_reports_open_target", table_name="reports")
        op.drop_index("ix_reports_status", table_name="reports")
    op.rename_table("reports", "reports_old")
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reporter_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(_one_of("target_type", targets), name="ck_reports_target_type"),
        sa.CheckConstraint(_one_of("reason", REPORT_REASONS), name="ck_reports_reason"),
        sa.CheckConstraint(_one_of("status", REPORT_STATUSES), name="ck_reports_status"),
    )
    names = ", ".join(REPORT_COLUMNS)
    op.execute(f"INSERT INTO reports ({names}) SELECT {names} FROM reports_old")
    op.drop_table("reports_old")
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_index(
        "uq_reports_open_target", "reports", ["reporter_id", "target_type", "target_id"], unique=True,
        sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"),
    )


def upgrade():
    # A downgrade that meets a repost report would fail the old CHECK. Drop
    # those rows only on the way down; nothing here deletes a report.
    _copy_reports(REPORT_TARGETS, True)

    op.create_table(
        "reposts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "quote IS NULL OR (length(quote) >= 1 AND length(quote) <= 280)",
            name="ck_reposts_quote",
        ),
    )
    op.create_index("ix_reposts_review_id", "reposts", ["review_id"])
    op.create_index(
        "uq_reposts_plain", "reposts", ["user_id", "review_id"], unique=True,
        sqlite_where=sa.text("quote IS NULL"), postgresql_where=sa.text("quote IS NULL"),
    )

    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "review_id", name="uq_bookmarks_pair"),
    )
    op.create_index("ix_bookmarks_user_id", "bookmarks", ["user_id"])


def downgrade():
    op.drop_index("ix_bookmarks_user_id", table_name="bookmarks")
    op.drop_table("bookmarks")
    op.drop_index("uq_reposts_plain", table_name="reposts")
    op.drop_index("ix_reposts_review_id", table_name="reposts")
    op.drop_table("reposts")
    op.execute("DELETE FROM reports WHERE target_type = 'repost'")
    op.drop_index("uq_reports_open_target", table_name="reports")
    op.drop_index("ix_reports_status", table_name="reports")
    _copy_reports(OLD_TARGETS, False)
