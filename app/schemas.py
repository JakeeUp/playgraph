from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    steam_appid: int
    name: str
    genres: str | None
    header_image_url: str | None


class LibraryEntryOut(BaseModel):
    """A single game in a user's library, joined with their latest playtime."""

    game: GameOut
    playtime_minutes: int
    achievements_unlocked: int | None
    achievements_total: int | None
    captured_at: datetime


class GenreBreakdownOut(BaseModel):
    """One slice of the genre-by-playtime graph. TODO(milestone 2)."""

    genre: str
    total_minutes: int
    game_count: int


class RecommendationOut(BaseModel):
    """TODO(milestone 4)."""

    game: GameOut
    score: float
    reason: str


class ReviewCreate(BaseModel):
    rating: float
    body: str | None = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    game_id: int
    rating: float
    body: str | None
    created_at: datetime
    verified_playtime_minutes: int | None
    verified_achievement_pct: float | None


class CommentCreate(BaseModel):
    body: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
