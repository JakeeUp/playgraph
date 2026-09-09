import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property

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
    # xbox / psn intentionally not supported in v1 - see README "Scope"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    display_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    linked_accounts = relationship("LinkedAccount", back_populates="user")
    reviews = relationship("Review", back_populates="user")


class LinkedAccount(Base):
    """A platform identity (currently only Steam) linked to a User.

    Kept separate from User rather than just storing steam_id on User so that
    supporting a second platform later (Xbox, etc.) doesn't require a schema
    migration that reshapes the users table - it's just a new row here.
    """

    __tablename__ = "linked_accounts"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(Enum(Platform), nullable=False)
    platform_user_id = Column(String, nullable=False)  # SteamID64
    linked_at = Column(DateTime(timezone=True), default=utcnow)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="linked_accounts")


class Game(Base):
    """A canonical game record, keyed by Steam's app ID.

    genres is a comma-separated string for v1 simplicity - worth revisiting
    as a proper many-to-many GameGenre table once genre-based querying
    (e.g. "all users' avg playtime in Roguelike games") actually needs to be
    fast, but not needed yet.
    """

    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    steam_appid = Column(Integer, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    genres = Column(String, nullable=True)  # e.g. "Action,Indie,RPG"
    header_image_url = Column(String, nullable=True)

    playtime_snapshots = relationship("PlaytimeSnapshot", back_populates="game")
    reviews = relationship("Review", back_populates="game")

    @hybrid_property
    def content_kind(self):
        from app.content import content_kind
        return content_kind(self.steam_appid, self.genres)

    @content_kind.expression
    def content_kind(cls):
        from app.content import content_kind_sql
        return content_kind_sql(cls)


class PlaytimeSnapshot(Base):
    """A point-in-time capture of a user's playtime/achievement progress for
    a game, taken by the sync job.

    Append-only (not upserted in place) so that playtime *over time* is
    queryable later - e.g. a "hours played this month" chart - not just a
    running total. A review's "verified" badge is computed from the most
    recent snapshot for that user+game.
    """

    __tablename__ = "playtime_snapshots"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    playtime_minutes = Column(Integer, default=0)
    achievements_unlocked = Column(Integer, nullable=True)
    achievements_total = Column(Integer, nullable=True)
    captured_at = Column(DateTime(timezone=True), default=utcnow)

    game = relationship("Game", back_populates="playtime_snapshots")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("user_id", "game_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    rating = Column(Float, nullable=False)  # 0.5-5.0 stars, Letterboxd-style
    body = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    # Denormalized at write-time from the user's latest PlaytimeSnapshot -
    # see routers/reviews.py. Kept on the review itself rather than
    # computed live so a review's "credibility" reflects what they'd actually
    # played *at the time they wrote it*, not their current playtime.
    verified_playtime_minutes = Column(Integer, nullable=True)
    verified_achievement_pct = Column(Float, nullable=True)

    user = relationship("User", back_populates="reviews")
    game = relationship("Game", back_populates="reviews")
    comments = relationship("Comment", back_populates="review")

    @property
    def author_name(self):
        return self.user.display_name


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    review = relationship("Review", back_populates="comments")
    user = relationship("User")

    @property
    def author_name(self):
        return self.user.display_name
