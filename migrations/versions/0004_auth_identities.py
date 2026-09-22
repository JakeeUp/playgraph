"""Separate provider identities from imported platform accounts.

No existing user, review, snapshot, or Steam association is rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_auth_identities"
down_revision = "0003_content_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("issuer", "subject"), sa.UniqueConstraint("user_id"))


def downgrade():
    raise RuntimeError("Export account identities before planning a manual downgrade")
