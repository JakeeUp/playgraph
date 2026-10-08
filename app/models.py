import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship

from app.content import content_kind as classify_content
from app.database import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    datetime.utcnow() is deprecated from Python 3.12 and slated for removal:
    it returns a NAIVE datetime that merely happens to hold UTC, which is a
    footgun the moment anything compares it against an aware one. Every
    timestamp in this schema is stored aware and in UTC so there is never a
    naive/aware mix to reconcile.
    """
    return datetime.now(timezone.utc)


class Platform(str, enum.Enum):
    steam = "steam"
    # PlayStation via a verified public profile; see docs/PSN_INTEGRATION.md.
    psn = "psn"


class SnapshotSource(str, enum.Enum):
    """Where a snapshot's numbers came from. Readers never add one source's
    minutes to another's, even for the same Game."""
    steam = "steam"
    psn = "psn"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    display_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    linked_accounts = relationship("LinkedAccount", back_populates="user")
    reviews = relationship("Review", back_populates="user")


class AuthIdentity(Base):
    """Authentication identity, distinct from permission to import a platform."""
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"), UniqueConstraint("user_id"))
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, nullable=False)
    issuer = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    user = relationship("User")


class LinkedAccount(Base):
    """A platform identity (Steam or PlayStation) linked to a User.

    Kept separate from User rather than just storing steam_id on User so that
    supporting a second platform later (Xbox, etc.) doesn't require a schema
    migration that reshapes the users table - it's just a new row here.
    """

    __tablename__ = "linked_accounts"
    # One user per platform account, and one account per platform per user. The
    # second rule is what stops two browser tabs connecting two Steam accounts.
    __table_args__ = (UniqueConstraint("platform", "platform_user_id"),
                      UniqueConstraint("user_id", "platform", name="uq_linked_accounts_user_platform"))

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(Enum(Platform), nullable=False)
    # SteamID64, or the numeric PSN accountId (stable; Online IDs can be renamed).
    platform_user_id = Column(String, nullable=False)
    linked_at = Column(DateTime(timezone=True), default=utcnow)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    # The platform's current public name, e.g. the PSN Online ID. Display only;
    # refreshed on every sync because players can rename themselves.
    display_handle = Column(String, nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verification_method = Column(String, nullable=True)  # "psn_about_me"; Steam rows: NULL

    user = relationship("User", back_populates="linked_accounts")


class Game(Base):
    """A canonical game record. Steam games carry steam_appid; games from other
    stores have it NULL and are found through game_external_ids instead.
    A PlayStation game is never merged into a Steam game by name; only an exact
    store-ID match through IGDB (provider 'igdb' in game_external_ids) can make
    two store records the same game.

    summary, first_release_date and cover_image_id come from IGDB and stay
    NULL until an IGDB refresh. genres keeps the store's own tags when it has
    them; IGDB genres only fill it when it is empty.

    genres is a comma-separated string for v1 simplicity - worth revisiting
    as a proper many-to-many GameGenre table once genre-based querying
    (e.g. "all users' avg playtime in Roguelike games") actually needs to be
    fast, but not needed yet.
    """

    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    steam_appid = Column(Integer, unique=True, nullable=True, index=True)
    name = Column(String, nullable=False)
    genres = Column(String, nullable=True)  # e.g. "Action,Indie,RPG"
    header_image_url = Column(String, nullable=True)
    # "game" or "software". Stored rather than derived in SQL, because the
    # catalog and feed filter on it for every row. The listener below keeps it
    # in step with steam_appid and genres on every insert and update.
    content_kind = Column(String, nullable=False, default="game", server_default="game", index=True)
    summary = Column(Text, nullable=True)
    first_release_date = Column(DateTime(timezone=True), nullable=True)
    cover_image_id = Column(String, nullable=True)  # IGDB image ID, never a URL
    hero_image_id = Column(String, nullable=True)  # IGDB artwork/screenshot ID for banners
    igdb_refreshed_at = Column(DateTime(timezone=True), nullable=True)

    playtime_snapshots = relationship("PlaytimeSnapshot", back_populates="game")
    reviews = relationship("Review", back_populates="game")
    external_ids = relationship("GameExternalId", back_populates="game")
    platforms = relationship("GamePlatform", back_populates="game", cascade="all, delete-orphan")


class GamePlatform(Base):
    """A platform a game was released on, by IGDB platform slug (e.g. "ps5")."""

    __tablename__ = "game_platforms"
    __table_args__ = (UniqueConstraint("game_id", "slug", name="uq_game_platforms_game_slug"),)

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    slug = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    abbreviation = Column(String, nullable=True)

    game = relationship("Game", back_populates="platforms")


class IgdbMatchCandidate(Base):
    """An IGDB match too uncertain to apply automatically, kept for the owner
    to accept or reject. reason: "name" (title match only) or "igdb_taken"
    (an exact store-ID match whose IGDB game already belongs to another
    PlayGraph game, i.e. a merge proposal). status: pending, accepted, rejected.
    """

    __tablename__ = "igdb_match_candidates"
    __table_args__ = (UniqueConstraint("game_id", "igdb_id", name="uq_igdb_match_candidates_game_igdb"),)

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    igdb_id = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending", index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class GameExternalId(Base):
    """A store's own ID for a Game. One store ID belongs to exactly one Game.

    providers: "psn_concept" (gamelist concept, groups regional and PS4/PS5
    title IDs), "psn_title" (CUSA.../PPSA... title ID), "psn_trophy"
    (NPWR..._00 trophy list) and "igdb" (IGDB game ID). Steam keeps using
    games.steam_appid.
    """

    __tablename__ = "game_external_ids"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_game_external_ids_provider_external_id"),)

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    external_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    game = relationship("Game", back_populates="external_ids")


@event.listens_for(Game, "before_insert")
@event.listens_for(Game, "before_update")
def _classify_game(_mapper, _connection, game):
    game.content_kind = classify_content(game.steam_appid, game.genres)


class PlaytimeSnapshot(Base):
    """A point-in-time capture of a user's playtime/achievement progress for
    a game, taken by the sync job.

    Append-only (not upserted in place) so that playtime *over time* is
    queryable later - e.g. a "hours played this month" chart - not just a
    running total. A review's "verified" badge is computed from the most
    recent snapshot for that user+game.
    """

    __tablename__ = "playtime_snapshots"
    # "Newest snapshot per game for this user" is read by the library, genre
    # graph, feed and every sync. Leading with user_id and game_id lets those
    # reads walk one user's rows already grouped by game; see migration 0006.
    __table_args__ = (
        Index("ix_playtime_snapshots_user_game_captured", "user_id", "game_id", "captured_at", "id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    # Which linked account and store produced these numbers. Readers group by
    # source so Steam and PlayStation minutes are never added together.
    linked_account_id = Column(Integer, ForeignKey("linked_accounts.id"), nullable=True)
    source = Column(String, nullable=False, default=SnapshotSource.steam.value,
                    server_default=SnapshotSource.steam.value)
    # NULL means unknown (PSN play time hidden or not reported), 0 means a
    # verified zero. Steam always reports a number. No default: a Python-side
    # default would silently turn an explicit None (unknown) into 0.
    playtime_minutes = Column(Integer, nullable=True)
    # Steam achievements, or for PSN the summed trophy counts, so views that
    # only know achievements still work.
    achievements_unlocked = Column(Integer, nullable=True)
    achievements_total = Column(Integer, nullable=True)
    trophies_bronze = Column(Integer, nullable=True)
    trophies_silver = Column(Integer, nullable=True)
    trophies_gold = Column(Integer, nullable=True)
    trophies_platinum = Column(Integer, nullable=True)
    trophies_bronze_total = Column(Integer, nullable=True)
    trophies_silver_total = Column(Integer, nullable=True)
    trophies_gold_total = Column(Integer, nullable=True)
    trophies_platinum_total = Column(Integer, nullable=True)
    trophy_progress = Column(Integer, nullable=True)  # Sony's own 0-100 figure
    captured_at = Column(DateTime(timezone=True), default=utcnow)

    game = relationship("Game", back_populates="playtime_snapshots")


class Review(Base):
    __tablename__ = "reviews"
    # Deleted discussion links must never resolve to a newly created review.
    __table_args__ = (UniqueConstraint("user_id", "game_id"), {"sqlite_autoincrement": True})

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    rating = Column(Float, nullable=False)  # 0.5-5.0 stars, Letterboxd-style
    body = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    # Denormalized at write-time from the user's latest PlaytimeSnapshot -
    # see routers/reviews.py. Kept on the review itself rather than
    # computed live so a review's "credibility" reflects what they'd actually
    # played *at the time they wrote it*, not their current playtime.
    verified_playtime_minutes = Column(Integer, nullable=True)
    verified_achievement_pct = Column(Float, nullable=True)
    # Which store the verified numbers came from ("steam" or "psn"), so a
    # review never presents PlayStation trophies as Steam achievements.
    verified_source = Column(String, nullable=True)

    user = relationship("User", back_populates="reviews")
    game = relationship("Game", back_populates="reviews")
    comments = relationship("Comment", back_populates="review")

    @property
    def author_name(self):
        return self.user.display_name


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    review = relationship("Review", back_populates="comments")
    user = relationship("User")

    @property
    def author_name(self):
        return self.user.display_name
