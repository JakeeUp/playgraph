"""Profiles, follows, blocks and reports.

Every existing row and ID is kept; this only adds columns and tables.
- users gains bio (NULL until someone writes one) and profile_visibility,
  which starts as 'public' for everyone, the same as today: anyone could
  already see a player's name and reviews.
- follows and blocks are pairs of user IDs, one row per direction, never
  pointing at yourself. A follow of a followers-only profile starts as
  'pending' until the followee accepts it.
- reports hold what players flagged for the owner. target_id is not a
  foreign key so a report survives the review or comment it is about. A
  partial unique index allows one open report per reporter and target.

This migration does not import application code.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_social"
down_revision = "0009_igdb_hero_art"
branch_labels = None
depends_on = None

REPORT_TARGETS = ("review", "comment", "user")
REPORT_REASONS = ("spam", "harassment", "hate", "sexual", "spoilers", "other")
REPORT_STATUSES = ("open", "dismissed", "actioned")
FOLLOW_STATUSES = ("pending", "accepted")


def _one_of(column, values):
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade():
    op.add_column("users", sa.Column("bio", sa.String(), nullable=True))
    op.add_column("users", sa.Column("profile_visibility", sa.String(), nullable=False, server_default="public"))

    op.create_table(
        "follows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("follower_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("followee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="accepted"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("follower_id", "followee_id", name="uq_follows_pair"),
        sa.CheckConstraint("follower_id <> followee_id", name="ck_follows_not_self"),
        sa.CheckConstraint(_one_of("status", FOLLOW_STATUSES), name="ck_follows_status"),
    )
    op.create_index("ix_follows_followee_id", "follows", ["followee_id"])

    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blocker_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("blocked_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_pair"),
        sa.CheckConstraint("blocker_id <> blocked_id", name="ck_blocks_not_self"),
    )
    op.create_index("ix_blocks_blocked_id", "blocks", ["blocked_id"])

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
        sa.CheckConstraint(_one_of("target_type", REPORT_TARGETS), name="ck_reports_target_type"),
        sa.CheckConstraint(_one_of("reason", REPORT_REASONS), name="ck_reports_reason"),
        sa.CheckConstraint(_one_of("status", REPORT_STATUSES), name="ck_reports_status"),
    )
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_index("uq_reports_open_target", "reports", ["reporter_id", "target_type", "target_id"], unique=True,
                    sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"))


def downgrade():
    op.drop_index("uq_reports_open_target", table_name="reports")
    op.drop_index("ix_reports_status", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_blocks_blocked_id", table_name="blocks")
    op.drop_table("blocks")
    op.drop_index("ix_follows_followee_id", table_name="follows")
    op.drop_table("follows")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("profile_visibility")
        batch.drop_column("bio")
