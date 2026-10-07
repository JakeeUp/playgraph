"""PlayStation accounts, store-neutral games and source-aware snapshots.

Every existing row and ID is kept. What changes:
- linked_accounts.platform accepts "psn". SQLite stores the enum as a plain
  VARCHAR(5) with no CHECK constraint (SQLAlchemy 1.4+ default), so only
  PostgreSQL's native "platform" type needs changing. It is rebuilt as a new
  type rather than with ALTER TYPE ... ADD VALUE, because a value added that
  way cannot be used until the transaction commits, and the Postgres copy tool
  (app/copy_to_postgres.py) migrates and inserts rows in one transaction.
- linked_accounts gains display_handle, verified_at and verification_method.
- games.steam_appid becomes nullable (still unique) and game_external_ids maps
  store IDs (PSN concept, title and trophy list IDs) to games.
- playtime_snapshots gains source (existing rows: 'steam'), linked_account_id
  (existing rows: the user's Steam link), trophy breakdown columns, and
  playtime_minutes may be NULL, meaning unknown.
- reviews gains verified_source; reviews that already carry verified numbers
  got them from Steam, so they are labelled 'steam'.

This migration does not import application code; the tables it touches are
described inline.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_psn_accounts_and_sources"
down_revision = "0006_snapshot_lookup_index"
branch_labels = None
depends_on = None

TROPHY_COLUMNS = ("trophies_bronze", "trophies_silver", "trophies_gold", "trophies_platinum",
                  "trophies_bronze_total", "trophies_silver_total", "trophies_gold_total",
                  "trophies_platinum_total", "trophy_progress")


def _platform_enum_accepts_psn(bind):
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE TYPE platform_v2 AS ENUM ('steam', 'psn')")
    op.execute("ALTER TABLE linked_accounts ALTER COLUMN platform TYPE platform_v2 "
               "USING platform::text::platform_v2")
    op.execute("DROP TYPE platform")
    op.execute("ALTER TYPE platform_v2 RENAME TO platform")


def upgrade():
    bind = op.get_bind()
    _platform_enum_accepts_psn(bind)

    # Plain ADD COLUMN works on both databases and keeps SQLite's table as is.
    op.add_column("linked_accounts", sa.Column("display_handle", sa.String(), nullable=True))
    op.add_column("linked_accounts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("linked_accounts", sa.Column("verification_method", sa.String(), nullable=True))
    op.add_column("reviews", sa.Column("verified_source", sa.String(), nullable=True))

    # SQLite cannot drop NOT NULL in place; batch mode copies the table with
    # every row, ID and index. PostgreSQL just alters the column.
    with op.batch_alter_table("games") as batch:
        batch.alter_column("steam_appid", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "game_external_ids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "external_id", name="uq_game_external_ids_provider_external_id"),
    )
    op.create_index("ix_game_external_ids_game_id", "game_external_ids", ["game_id"])

    with op.batch_alter_table("playtime_snapshots") as batch:
        batch.add_column(sa.Column("linked_account_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="steam"))
        for name in TROPHY_COLUMNS:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
        batch.alter_column("playtime_minutes", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key("fk_playtime_snapshots_linked_account_id", "linked_accounts",
                                 ["linked_account_id"], ["id"])

    # Every snapshot so far came from a Steam sync. Attach it to the user's
    # Steam link (one per user since 0005); a user with no link stays NULL.
    op.execute(
        "UPDATE playtime_snapshots SET linked_account_id = ("
        " SELECT linked_accounts.id FROM linked_accounts"
        " WHERE linked_accounts.user_id = playtime_snapshots.user_id"
        " AND linked_accounts.platform = 'steam')"
        " WHERE linked_account_id IS NULL"
    )
    op.execute(
        "UPDATE reviews SET verified_source = 'steam'"
        " WHERE verified_source IS NULL"
        " AND (verified_playtime_minutes IS NOT NULL OR verified_achievement_pct IS NOT NULL)"
    )


def downgrade():
    raise RuntimeError("PlayStation links and snapshots would be lost. Restore a verified backup instead.")
