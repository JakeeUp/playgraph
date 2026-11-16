"""@handles, review likes and pinned favorite games.

Every existing row and ID is kept.
- users gains handle, unique. Each existing player gets one made from their
  display name: lowercase ASCII letters, digits and underscores. A name that is
  empty, too short, all digits or reserved becomes "player". When the result is
  already taken, the player's ID is appended, which can never collide.
  Players can change it later from their profile.
- review_likes: one row per player and review.
- favorites: up to four games per player, in slots 1 to 4.

This migration does not import application code; the handle rules here are a
frozen copy of app/handles.py as of this revision.
"""
import re
import unicodedata

from alembic import op
import sqlalchemy as sa

revision = "0012_handles_likes_favorites"
down_revision = "0011_game_statuses"
branch_labels = None
depends_on = None

PATTERN = re.compile(r"^(?=[a-z0-9_]*[a-z])[a-z0-9_]{3,15}$")
RESERVED = {
    "about", "api", "app", "assets", "demo", "everyone", "help", "here", "home", "me", "null", "root",
    "security", "settings", "sony", "steam", "system", "team", "undefined", "valve", "igdb", "psn",
    "playstation", "xbox", "nintendo", "account", "login", "logout", "signup", "news", "feed", "explore",
}
RESERVED_PARTS = ("playgraph", "admin", "moderator", "official", "support", "staff", "owner")


def _usable(handle):
    return bool(PATTERN.fullmatch(handle)) and handle not in RESERVED and not any(p in handle for p in RESERVED_PARTS)


def _base(name):
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9_]", "", plain.replace(" ", "_")).strip("_")[:15]
    return base if _usable(base) else "player"


def upgrade():
    op.add_column("users", sa.Column("handle", sa.String(15), nullable=True))
    connection = op.get_bind()
    taken = set()
    for user_id, name in connection.execute(sa.text("SELECT id, display_name FROM users ORDER BY id")).all():
        handle = _base(name)
        if handle in taken:
            suffix = str(user_id)
            handle = handle[:15 - len(suffix)] + suffix
        taken.add(handle)
        connection.execute(sa.text("UPDATE users SET handle = :handle WHERE id = :id"), {"handle": handle, "id": user_id})
    op.create_index("ix_users_handle", "users", ["handle"], unique=True)

    op.create_table(
        "review_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("reviews.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "review_id", name="uq_review_likes_pair"),
    )
    op.create_index("ix_review_likes_review_id", "review_likes", ["review_id"])

    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "position", name="uq_favorites_slot"),
        sa.UniqueConstraint("user_id", "game_id", name="uq_favorites_game"),
        sa.CheckConstraint("position BETWEEN 1 AND 4", name="ck_favorites_position"),
    )


def downgrade():
    op.drop_table("favorites")
    op.drop_index("ix_review_likes_review_id", table_name="review_likes")
    op.drop_table("review_likes")
    op.drop_index("ix_users_handle", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("handle")
