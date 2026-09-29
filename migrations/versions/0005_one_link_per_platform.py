"""Allow one linked account per platform for each user.

Connecting Steam is a browser round trip, so two tabs can finish at once. A
code check alone cannot stop both committing on Postgres; this constraint makes
the database refuse the second, so a user never mixes two Steam libraries.
"""
from alembic import op

revision = "0005_one_link_per_platform"
down_revision = "0004_auth_identities"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("linked_accounts") as batch:
        batch.create_unique_constraint("uq_linked_accounts_user_platform", ["user_id", "platform"])


def downgrade():
    with op.batch_alter_table("linked_accounts") as batch:
        batch.drop_constraint("uq_linked_accounts_user_platform", type_="unique")
