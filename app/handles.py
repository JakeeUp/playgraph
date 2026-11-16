"""Public @handles: unique, short and plain enough that nobody can pass as someone else.

A handle is 3 to 15 characters of lowercase a-z, 0-9 and underscore, with at
least one letter, so it can never be mistaken for a numeric user ID. ASCII
only, which rules out lookalike letters from other alphabets. Names that could
pass as the site or its staff are reserved, and so is anything containing them
("playgraph_help", "the_admin").

Display names stay free text and can repeat; the handle is what identifies a player.
"""
import re
import secrets
import unicodedata

from sqlalchemy.orm import Session

HANDLE_MIN, HANDLE_MAX = 3, 15
PATTERN = re.compile(r"^(?=[a-z0-9_]*[a-z])[a-z0-9_]{3,15}$")
RESERVED = frozenset({
    "about", "api", "app", "assets", "demo", "everyone", "help", "here", "home", "me", "null", "root",
    "security", "settings", "sony", "steam", "system", "team", "undefined", "valve", "igdb", "psn",
    "playstation", "xbox", "nintendo", "account", "login", "logout", "signup", "news", "feed", "explore",
})
RESERVED_PARTS = ("playgraph", "admin", "moderator", "official", "support", "staff", "owner")


def normalize(value: str) -> str:
    """What a player typed, as a handle: trimmed, without a leading @, lowercase."""
    return value.strip().removeprefix("@").lower()


def problem(handle: str) -> str | None:
    """Why this (normalized) handle can't be used, or None if it can."""
    if not PATTERN.fullmatch(handle):
        return f"Use {HANDLE_MIN} to {HANDLE_MAX} letters, numbers or underscores, with at least one letter."
    if handle in RESERVED or any(part in handle for part in RESERVED_PARTS):
        return "That handle is reserved. Try another."
    return None


def base_for(display_name: str) -> str:
    """A starting handle from a display name: accents dropped, other characters removed."""
    plain = unicodedata.normalize("NFKD", display_name or "").encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9_]", "", plain.replace(" ", "_")).strip("_")[:HANDLE_MAX]
    return base if problem(base) is None else "player"


def available(db: Session, display_name: str) -> str:
    """A free handle for a new player. Tries the name itself, then the name
    with a random number. The unique constraint still has the final word."""
    from app.models import User

    base = base_for(display_name)
    candidates = [base] + [f"{base[:HANDLE_MAX - 4]}{secrets.randbelow(9000) + 1000}" for _ in range(8)]
    for candidate in candidates:
        if db.query(User.id).filter(User.handle == candidate).first() is None:
            return candidate
    return f"player{secrets.token_hex(4)}"[:HANDLE_MAX]
