"""Conservative software classification shared by SQL and API serialization.

Steam's appdetails type alone mislabels some software as games. These explicit
IDs and software-only genre tokens use Steam store evidence. Replace the
heuristic with a versioned metadata source when catalog enrichment is added.
"""
from sqlalchemy import case, func, literal, or_

SOFTWARE_APP_IDS = frozenset({1812620, 400040, 431960})  # DSX, ShareX, Wallpaper Engine
SOFTWARE_GENRES = frozenset({
    "utilities", "animation&modeling", "design&illustration", "photoediting",
    "audioproduction", "videoproduction", "webpublishing", "softwaretraining",
    "education", "accounting", "gamedevelopment",
})


def normalize_genre(value: str) -> str:
    return value.lower().translate(str.maketrans("", "", " \t\r\n"))


def content_kind(appid: int, genres: str | None) -> str:
    tokens = {normalize_genre(token) for token in (genres or "").split(",")}
    return "software" if appid in SOFTWARE_APP_IDS or tokens & SOFTWARE_GENRES else "game"


def content_kind_sql(model):
    normalized = func.lower(func.coalesce(model.genres, ""))
    for whitespace in " \t\r\n":
        normalized = func.replace(normalized, whitespace, "")
    tokens = literal(",") + normalized + literal(",")
    software = or_(model.steam_appid.in_(SOFTWARE_APP_IDS),
                   *(tokens.contains(f",{genre},", autoescape=True) for genre in sorted(SOFTWARE_GENRES)))
    return case((software, "software"), else_="game")
