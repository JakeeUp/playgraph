"""Gaming headlines from APITube.

APITube has no gaming topic or category, so relevance comes from the sources:
everything these outlets publish is about games. The API accepts at most three
domains per request and treats them as either/or. These three are ones APITube
still indexes daily; its IGN and Polygon coverage stopped in 2025.

Only the fields the page shows are kept, and only https links, so nothing the
upstream sends can turn into a javascript: or http: link on the page. Images
are kept only from the outlets' own image hosts, which the page's CSP allows.
"""

import re
from urllib.parse import urlsplit

import httpx

from app.config import settings

NEWS_URL = "https://api.apitube.io/v1/news/everything"
SOURCES = ("pcgamer.com", "eurogamer.net", "rockpapershotgun.com")
PER_PAGE = 10  # the free APITube plan rejects anything above 10
# Where these outlets serve article images; the /app CSP allows the same hosts.
IMAGE_HOSTS = ("futurecdn.net", "gnwcdn.com")
# PC Gamer descriptions open with the photo caption, which says nothing.
CREDIT = re.compile(r"^\s*\(Image credit:[^)]*\)\s*", re.IGNORECASE)


def configured() -> bool:
    return bool(settings.apitube_api_key.get_secret_value())


def _https(value) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    url = urlsplit(value)
    return value if url.scheme == "https" and url.hostname else None


def _image(value) -> str | None:
    url = _https(value)
    host = urlsplit(url).hostname if url else ""
    return url if any(host.endswith("." + allowed) for allowed in IMAGE_HOSTS) else None


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def parse(payload: dict) -> list[dict]:
    """Reduce APITube results to headline rows, newest first, one per title."""
    items, seen = [], set()
    for article in payload.get("results") or []:
        if not isinstance(article, dict):
            continue
        href, title = _https(article.get("href")), _text(article.get("title"), 200)
        key = title.casefold()
        if not href or not title or key in seen:
            continue
        seen.add(key)
        source = article.get("source") if isinstance(article.get("source"), dict) else {}
        items.append({"title": title, "url": href,
                      "source": _text(source.get("domain"), 80),
                      "published_at": _text(article.get("published_at"), 40),
                      "summary": _text(CREDIT.sub("", _text(article.get("description"), 2000)), 280),
                      "image": _image(article.get("image"))})
    return items


def fetch_gaming_news() -> list[dict]:
    """Blocking; the cache runs it on a worker thread."""
    params = {"source.domain": ",".join(SOURCES), "language.code": "en",
              "sort.by": "published_at", "sort.order": "desc", "per_page": PER_PAGE}
    with httpx.Client(timeout=10) as client:
        response = client.get(NEWS_URL, params=params,
                              headers={"X-API-Key": settings.apitube_api_key.get_secret_value()})
        response.raise_for_status()
    return parse(response.json())
