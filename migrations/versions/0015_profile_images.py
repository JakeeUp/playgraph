"""Two replaceable, normalized profile images per player."""
from alembic import op
import sqlalchemy as sa

revision = "0015_profile_images"
down_revision = "0014_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", name="uq_profile_images_user_kind"),
        sa.CheckConstraint("kind IN ('avatar', 'cover')", name="ck_profile_images_kind"),
    )


def downgrade():
    op.drop_table("profile_images")
