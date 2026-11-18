import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    exists,
    func,
    select,
    text,
)
from sqlalchemy.orm import column_property, deferred, relationship

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
    # PlayStation via a verified public profile.
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
    # Shown on the profile page only. Cleaned like display_name, at most 280 characters.
    bio = Column(String, nullable=True)
    # Who can open the profile page: "public", "followers" (basics only for
    # everyone else) or "private" (nobody but the owner). Reviews stay public
    # posts either way; this governs the profile and its follow lists.
    profile_visibility = Column(String, nullable=False, default="public", server_default="public")
    # The public @handle: lowercase a-z, 0-9 and underscore, 3 to 15
    # characters, unique. Display names can repeat; handles cannot. Rules and
    # the reserved list live in app/handles.py.
    handle = Column(String(15), nullable=True, unique=True, index=True)

    linked_accounts = relationship("LinkedAccount", back_populates="user")
    reviews = relationship("Review", back_populates="user")

    @property
    def badge(self) -> int | None:
        """The verified badge this player has earned: 100, 1000 or 5000, or None."""
        return badge_for(self.badge_minutes)

    @property
    def avatar_url(self):
        return f"/users/{self.id}/images/avatar?v={self.avatar_key}" if self.avatar_key else None

    @property
    def cover_url(self):
        return f"/users/{self.id}/images/cover?v={self.cover_key}" if self.cover_key else None


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
    __table_args__ = (Index("ix_games_catalog_name", "content_kind", "name", "id"),)

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
    added_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    added_at = Column(DateTime(timezone=True), nullable=True)

    @property
    def catalog_source(self):
        return "steam" if self.steam_appid is not None else "igdb" if self.added_at is not None else "psn"

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


class CatalogSyncState(Base):
    """One durable IGDB cursor, committed with each page of public metadata."""

    __tablename__ = "catalog_sync_state"

    id = Column(Integer, primary_key=True)
    after_id = Column(Integer, nullable=False, default=0, server_default="0")
    updated_after = Column(Integer, nullable=False, default=0, server_default="0")
    window_end = Column(Integer, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


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

    @property
    def author_handle(self):
        return self.user.handle

    @property
    def author_badge(self):
        return self.user.badge


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


PROFILE_VISIBILITY = ("public", "followers", "private")
REPORT_TARGETS = ("review", "comment", "user", "repost")
REPORT_REASONS = ("spam", "harassment", "hate", "sexual", "spoilers", "other")
REPORT_STATUSES = ("open", "dismissed", "actioned")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


FOLLOW_STATUSES = ("pending", "accepted")


class Follow(Base):
    """follower_id follows followee_id. A block removes both directions.

    Following a public profile is accepted at once. Following a followers-only
    profile starts as a pending request that the followee accepts or declines;
    only accepted follows count for anything.
    """

    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_follows_pair"),
        CheckConstraint("follower_id <> followee_id", name="ck_follows_not_self"),
        CheckConstraint(_one_of("status", FOLLOW_STATUSES), name="ck_follows_status"),
    )

    id = Column(Integer, primary_key=True)
    follower_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    followee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="accepted", server_default="accepted")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Block(Base):
    """blocker_id never sees blocked_id's reviews or comments, and the reverse.

    Nobody is told they were blocked: the blocker's profile simply looks like
    it does not exist to them.
    """

    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_pair"),
        CheckConstraint("blocker_id <> blocked_id", name="ck_blocks_not_self"),
    )

    id = Column(Integer, primary_key=True)
    blocker_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    blocked_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


NOTIFICATION_KINDS = ("like", "repost", "quote", "comment", "follow", "follow_accepted")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_one_of("kind", NOTIFICATION_KINDS), name="ck_notifications_kind"),
        CheckConstraint("recipient_id <> actor_id", name="ck_notifications_not_self"),
        CheckConstraint(
            "(kind IN ('follow', 'follow_accepted') AND review_id IS NULL AND comment_id IS NULL) OR "
            "(kind IN ('like', 'repost', 'quote') AND review_id IS NOT NULL AND comment_id IS NULL) OR "
            "(kind = 'comment' AND review_id IS NOT NULL AND comment_id IS NOT NULL)",
            name="ck_notifications_target",
        ),
        Index("ix_notifications_recipient_created", "recipient_id", "created_at", "id"),
        Index("ix_notifications_created_at", "created_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String, nullable=False)
    review_id = Column(Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=True)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)


class Report(Base):
    """A player's report of a review, comment or player, for the owner to review.

    target_id is not a foreign key: a report outlives the review or comment it
    is about, so the owner can still see that something was reported and removed.
    One open report per reporter and target; a closed one can be filed again.
    """

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(_one_of("target_type", REPORT_TARGETS), name="ck_reports_target_type"),
        CheckConstraint(_one_of("reason", REPORT_REASONS), name="ck_reports_reason"),
        CheckConstraint(_one_of("status", REPORT_STATUSES), name="ck_reports_status"),
        Index("uq_reports_open_target", "reporter_id", "target_type", "target_id", unique=True,
              sqlite_where=text("status = 'open'"), postgresql_where=text("status = 'open'")),
    )

    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    note = Column(String(500), nullable=True)
    status = Column(String, nullable=False, default="open", server_default="open", index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


PLAY_STATUSES = ("want_to_play", "playing", "completed", "paused", "dropped")


class GameStatus(Base):
    """Where a player says they are with a game: want to play, playing,
    completed, paused or dropped. One row per player and game; no row means
    no status.

    Set by hand only. Syncs never read or write this table, so a status
    survives every resync. It is not derived from achievements either: 100%
    achievements is not the same as finishing a game, and plenty of finished
    games never reach it. Any catalog game can carry one, owned or not, since
    "want to play" is mostly games the player doesn't have yet.

    completed_at is set when the status becomes completed and cleared when it
    moves away, so a year in review can count games finished that year.
    """

    __tablename__ = "game_statuses"
    __table_args__ = (
        UniqueConstraint("user_id", "game_id", name="uq_game_statuses_user_game"),
        CheckConstraint(_one_of("status", PLAY_STATUSES), name="ck_game_statuses_status"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    status = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    game = relationship("Game")


class ReviewLike(Base):
    """One player liking one review. Counts are public; who liked what is not
    exposed to anyone but the player who liked it."""

    __tablename__ = "review_likes"
    __table_args__ = (UniqueConstraint("user_id", "review_id", name="uq_review_likes_pair"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# Loaded with every review, so every response that shows a review carries its
# count without each route counting again. review_likes is indexed by review.
Review.like_count = column_property(
    select(func.count(ReviewLike.id)).where(ReviewLike.review_id == Review.id)
    .correlate_except(ReviewLike).scalar_subquery()
)

MAX_FAVORITES = 4


class Favorite(Base):
    """A game pinned to a player's profile, in slots 1 to 4. Slot 1 wears the crown."""

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "position", name="uq_favorites_slot"),
        UniqueConstraint("user_id", "game_id", name="uq_favorites_game"),
        CheckConstraint(f"position BETWEEN 1 AND {MAX_FAVORITES}", name="ck_favorites_position"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    position = Column(Integer, nullable=False)

    game = relationship("Game")


QUOTE_MAX = 280


class Repost(Base):
    """One player sharing someone else's review.

    quote is NULL for a plain repost, and one plain repost per player and
    review. A quote review is its own row, so the same player can quote a
    review more than once. Reposting your own review is refused in the route:
    a check here cannot see the review's author.
    """

    __tablename__ = "reposts"
    __table_args__ = (
        CheckConstraint(
            f"quote IS NULL OR (length(quote) >= 1 AND length(quote) <= {QUOTE_MAX})",
            name="ck_reposts_quote",
        ),
        Index("uq_reposts_plain", "user_id", "review_id", unique=True,
              sqlite_where=text("quote IS NULL"), postgresql_where=text("quote IS NULL")),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False, index=True)
    quote = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User")
    review = relationship("Review")


# Same idea as like_count: every review response can show it, and the cache
# stays shared because the viewer's own repost state comes from /me/reposts.
Review.repost_count = column_property(
    select(func.count(Repost.id)).where(Repost.review_id == Review.id)
    .correlate_except(Repost).scalar_subquery()
)


class Bookmark(Base):
    """A private save of a review. Only the owner can list theirs. No public count."""

    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "review_id", name="uq_bookmarks_pair"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    review = relationship("Review")


# Verified badges, in hours of verified play across a player's game reviews.
BADGE_HOURS = (100, 1000, 5000)


def badge_for(minutes: int | None) -> int | None:
    """The highest badge tier these verified minutes reach, or None below the first."""
    earned = [hours for hours in BADGE_HOURS if (minutes or 0) >= hours * 60]
    return earned[-1] if earned else None


# Computed on every load and never stored, so a badge can't go stale or be
# granted by hand. It counts the play time frozen on the player's own reviews,
# which only a Steam or PlayStation sync can write, and only while such an
# account is linked. Reviews of software are left out, like everywhere else
# software hours stay out of game stats: an app left running is not play.
User.badge_minutes = column_property(
    select(func.coalesce(func.sum(Review.verified_playtime_minutes), 0))
    .join(Game, Game.id == Review.game_id)
    .where(Review.user_id == User.id, Game.content_kind == "game",
           exists().where(LinkedAccount.user_id == Review.user_id))
    .correlate_except(Review, Game)
    .scalar_subquery()
)


class ProfileImage(Base):
    __tablename__ = "profile_images"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_profile_images_user_kind"),
        CheckConstraint("kind IN ('avatar', 'cover')", name="ck_profile_images_kind"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String, nullable=False)
    key = Column(String(32), nullable=False)
    data = deferred(Column(LargeBinary, nullable=False))


User.avatar_key = column_property(select(ProfileImage.key).where(
    ProfileImage.user_id == User.id, ProfileImage.kind == "avatar"
).correlate_except(ProfileImage).scalar_subquery())
User.cover_key = column_property(select(ProfileImage.key).where(
    ProfileImage.user_id == User.id, ProfileImage.kind == "cover"
).correlate_except(ProfileImage).scalar_subquery())
Review.author_avatar_url = property(lambda self: self.user.avatar_url)
