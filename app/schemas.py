import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # NULL for games from other stores (PlayStation); those have no Steam page or art.
    steam_appid: int | None
    name: str
    genres: str | None
    header_image_url: str | None
    content_kind: Literal["game", "software"]


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
