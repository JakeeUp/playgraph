"""Conservative software classification, applied when a game's genres are written.

Steam's appdetails type alone mislabels some software as games. These explicit
IDs and software-only genre tokens use Steam store evidence. Replace the
heuristic with a versioned metadata source when catalog enrichment is added.

The result is stored on games.content_kind (see app/models.py) instead of being
computed in SQL, where string functions on every joined row made the catalog
and feed queries roughly 80 times slower.
"""

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
