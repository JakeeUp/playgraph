"""Official announcements for games, from Steam's news API.

GetNewsForApp needs no key and has no daily quota worth worrying about, so it
can feed the For You page far more often than APITube. Only the developers'
own Steam announcements are asked for: the third-party feeds Steam mixes in
are often in other languages and repeat what the outlets already say.

Posts are BBCode. The page gets plain text, an https link, and an image URL
rebuilt from the post's clan image id, so nothing the post contains is passed
through as markup or as an arbitrary image address.
"""

import asyncio
import html
import re
from datetime import UTC, datetime

import httpx

NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
PER_GAME = 3
CONCURRENCY = 8
# Bound on a whole refresh. The caller holds the news lock meanwhile, so a slow
# Steam must cost visitors seconds, not a minute. Games not done by then are
# dropped from this refresh; if none finished, the refresh counts as failed.
DEADLINE = 20.0
# Where Steam serves announcement images; the /app CSP allows this host.
CLAN_IMAGES = "https://clan.fastly.steamstatic.com/images"
CLAN_IMAGE = re.compile(r"\{STEAM_CLAN_IMAGE\}/(\d+)/([0-9a-f]+\.(?:jpe?g|png|gif|webp))", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{STEAM_[A-Z_]+\}\S*")
MARKUP = re.compile(r"\[[^\]]*\]|<[^>]*>")


def _plain(contents: str, limit: int = 280) -> str:
    # \[ is a literal bracket in Steam's BBCode; keep it out of the tag pattern.
    text = contents.replace("\\[", "\0").replace("\\]", "\1")
    text = PLACEHOLDER.sub(" ", MARKUP.sub(" ", text)).replace("\0", "[").replace("\1", "]")
    text = " ".join(html.unescape(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse(payload: dict, appid: int, name: str) -> list[dict]:
    items = []
    for post in ((payload.get("appnews") or {}).get("newsitems") or [])[:PER_GAME]:
        if not isinstance(post, dict):
            continue
        url, title = post.get("url"), " ".join(str(post.get("title") or "").split())[:200]
        date, contents = post.get("date"), str(post.get("contents") or "")
        if not isinstance(url, str) or not url.startswith("https://") or not title or not isinstance(date, int):
            continue
        image = CLAN_IMAGE.search(contents)
        items.append({"title": title, "url": url, "source": name[:80],
                      "published_at": datetime.fromtimestamp(date, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "summary": _plain(contents),
                      "image": f"{CLAN_IMAGES}/{image[1]}/{image[2]}" if image else None,
                      "appid": appid})
    return items


async def fetch(games: list[tuple[int, str]]) -> list[dict]:
    """News for each (appid, name). One game failing only drops that game."""
    if not games:
        return []
    limit = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=15) as client:
        async def one(appid: int, name: str) -> list[dict]:
            async with limit:
                response = await client.get(NEWS_URL, params={
                    "appid": appid, "count": PER_GAME, "feeds": "steam_community_announcements"})
                response.raise_for_status()
                return parse(response.json(), appid, name)
        tasks = [asyncio.create_task(one(appid, name)) for appid, name in games]
        done, pending = await asyncio.wait(tasks, timeout=DEADLINE)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    results = [task.result() for task in done if not task.cancelled() and task.exception() is None]
    if not results:
        failures = [task.exception() for task in done if not task.cancelled() and task.exception()]
        if failures:
            raise failures[0]
        raise TimeoutError("Steam news refresh passed its deadline")
    return [item for result in results for item in result]
