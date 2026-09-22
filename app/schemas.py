from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    steam_appid: int
    name: str
    genres: str | None
    header_image_url: str | None
    content_kind: Literal["game", "software"]


class LibraryEntryOut(BaseModel):
    """A single game in a user's library, joined with their latest playtime."""

    game: GameOut
    playtime_minutes: int
    achievements_unlocked: int | None
    achievements_total: int | None
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
        return (value or "").strip() or None


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


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment must contain text")
        return value


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: int
    user_id: int
    author_name: str
    body: str
    created_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
