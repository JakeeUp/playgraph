import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import handles
from app.services.igdb import cover_url


def storable(value: str) -> str:
    # Postgres cannot store a NUL character in text; turn it away as bad input.
    if "\x00" in value:
        raise ValueError("Text contains a character that cannot be saved")
    return value


# C0/C1 controls, zero-width marks, bidi embeddings/overrides/isolates, BOM.
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
DISPLAY_NAME_MAX = 80


def display_name(value, fallback: str, limit: int = DISPLAY_NAME_MAX) -> str:
    """A provider-supplied name made safe to store and show publicly.

    Strips NUL (Postgres rejects it), other control characters and bidi
    overrides (which can visually reorder text on review cards), collapses
    whitespace and caps the length. Never raises: an unusable name becomes
    the fallback, so a strange provider profile cannot block sign-in.
    """
    if not isinstance(value, str):
        return fallback
    cleaned = _UNSAFE_NAME_CHARS.sub("", " ".join(unicodedata.normalize("NFC", value).split()))
    return cleaned[:limit].strip() or fallback


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Other sources have no Steam page or Steam art.
    steam_appid: int | None
    catalog_source: Literal["steam", "psn", "igdb"] = "steam"
    name: str
    genres: str | None
    header_image_url: str | None
    content_kind: Literal["game", "software"]
    first_release_date: datetime | None = None
    # IGDB cover art; the frontend uses it when a game has no Steam art. Built
    # from the stored image ID, which itself is never sent.
    cover_url: str | None = None
    cover_image_id: str | None = Field(default=None, exclude=True)
    # Wide IGDB art for banners, used when a game has no Steam hero art.
    hero_url: str | None = None
    hero_image_id: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def build_image_urls(self):
        if self.cover_url is None:
            self.cover_url = cover_url(self.cover_image_id)
        if self.hero_url is None:
            self.hero_url = cover_url(self.hero_image_id, "t_1080p")
        return self


class PlatformOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    abbreviation: str | None


class GameDetailOut(GameOut):
    """One game's page. Summary and platforms stay off list responses, which
    can carry thousands of games."""

    summary: str | None = None
    platforms: list[PlatformOut] = []


class TrophyCountsOut(BaseModel):
    platinum: int
    gold: int
    silver: int
    bronze: int


class TrophiesOut(BaseModel):
    """PlayStation trophies for one game: earned and defined counts per grade."""

    earned: TrophyCountsOut
    total: TrophyCountsOut
    progress: int | None  # Sony's own percentage; None when a game has several trophy lists


class LibraryEntryOut(BaseModel):
    """One game from one source in a user's library, with its latest snapshot.

    A game can appear once per source. Steam and PlayStation numbers are never
    added together; playtime_minutes is None when the source did not report it
    (PlayStation privacy settings, or a PS3/Vita trophy list).
    """

    game: GameOut
    source: Literal["steam", "psn"] = "steam"
    playtime_minutes: int | None
    achievements_unlocked: int | None
    achievements_total: int | None
    trophies: TrophiesOut | None = None
    captured_at: datetime


PlayStatus = Literal["want_to_play", "playing", "completed", "paused", "dropped"]


class StatusUpdate(BaseModel):
    """A play status chosen by the player. One of five fixed values, never free text."""

    model_config = ConfigDict(extra="forbid")

    status: PlayStatus


class GameStatusOut(BaseModel):
    """A player's status for one game. Carries the game card so a backlog of
    games the player doesn't own can still be drawn."""

    model_config = ConfigDict(from_attributes=True)

    game: GameOut
    status: PlayStatus
    updated_at: datetime
    completed_at: datetime | None = None


class GenreBreakdownOut(BaseModel):
    """One slice of the genre-by-playtime breakdown.

    total_minutes is full credit per genre, so summing these across all rows
    gives more than real playtime. See app/routers/library.py for why.
    """

    genre: str
    total_minutes: int
    game_count: int


class RecommendationOut(BaseModel):
    """A recommended game and why it was picked. Not built yet."""

    game: GameOut
    score: float
    reason: str


class ReviewCreate(BaseModel):
    rating: float = Field(ge=0.5, le=5.0, multiple_of=0.5)
    body: str | None = Field(default=None, max_length=10000)

    @field_validator("body")
    @classmethod
    def blank_body_is_none(cls, value: str | None) -> str | None:
        # A rating can stand on its own, so whitespace-only text is stored as
        # no text instead of rendering as an empty block under the stars.
        return storable((value or "").strip()) or None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    author_name: str
    game_id: int
    rating: float
    body: str | None
    created_at: datetime
    verified_playtime_minutes: int | None
    verified_achievement_pct: float | None
    # "steam" or "psn": which store the verified numbers came from. For PSN the
    # percentage is trophies earned, not Steam achievements.
    verified_source: Literal["steam", "psn"] | None = None
    author_handle: str | None = None
    # The author's verified badge: 100, 1000 or 5000 verified hours, or None.
    author_badge: int | None = None
    author_avatar_url: str | None = None
    # Public counts. Whether the viewer liked, reposted or saved it comes from
    # /me/likes, /me/reposts and /me/bookmarks, so this response stays the same
    # for everyone and can be cached. Bookmarks have no count: they are private.
    like_count: int = 0
    repost_count: int = 0


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment must contain text")
        return storable(value)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: int
    user_id: int
    author_name: str
    body: str
    created_at: datetime


BIO_MAX = 280


class ProfileUpdate(BaseModel):
    """Only the fields sent are changed. A blank bio clears it."""

    model_config = ConfigDict(extra="forbid")

    bio: str | None = Field(default=None, max_length=BIO_MAX * 2)
    profile_visibility: Literal["public", "followers", "private"] | None = None
    handle: str | None = Field(default=None, max_length=40)

    @field_validator("handle")
    @classmethod
    def check_handle(cls, value: str | None) -> str | None:
        if value is None:
            return None
        handle = handles.normalize(value)
        if reason := handles.problem(handle):
            raise ValueError(reason)
        return handle

    @field_validator("bio")
    @classmethod
    def clean_bio(cls, value: str | None) -> str | None:
        # Same cleaning as a display name: no control or bidi characters,
        # whitespace collapsed. The raw limit allows for whitespace that collapses.
        if value is None:
            return None
        return display_name(value, "", BIO_MAX) or None


class UserCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    handle: str | None = None
    badge: int | None = None
    avatar_url: str | None = None


class FavoritesUpdate(BaseModel):
    """The whole ordered list of pinned games. Slot 1 is the crowned pick."""

    model_config = ConfigDict(extra="forbid")

    game_ids: list[int] = Field(max_length=4)

    @field_validator("game_ids")
    @classmethod
    def distinct(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("Each game can be pinned once.")
        if any(not 1 <= game_id <= 2_147_483_647 for game_id in value):
            raise ValueError("Unknown game.")
        return value


class RepostCreate(BaseModel):
    """A missing or blank quote is a plain repost. Text makes a quote review."""

    model_config = ConfigDict(extra="forbid")

    quote: str | None = Field(default=None, max_length=560)

    @field_validator("quote")
    @classmethod
    def clean_quote(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Same cleaning as a bio: no control or bidi characters, whitespace collapsed.
        return display_name(value, "", 280) or None


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["review", "comment", "user", "repost"]
    target_id: int = Field(ge=1, le=2_147_483_647)
    reason: Literal["spam", "harassment", "hate", "sexual", "spoilers", "other"]
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return storable((value or "").strip()) or None


class ReportResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["dismissed", "actioned"]
