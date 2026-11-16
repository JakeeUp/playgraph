"""Invite-only private beta.

With BETA_INVITE_ONLY on, a sign-in may only CREATE a new PlayGraph user when
the Steam ID or the verified primary Clerk email is on the invite list. Users
that already exist keep signing in normally, so removing someone from the list
never locks out an account that is already there.
"""
from fastapi import HTTPException

from app.config import settings

NOT_INVITED = "PlayGraph is in a private beta. Ask for an invite to join."


def _entries(raw: str) -> set[str]:
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def steam_invited(steam_id: str) -> bool:
    return not settings.beta_invite_only or steam_id.lower() in _entries(settings.beta_steam_ids)


def verified_primary_email(account: dict) -> str | None:
    """The primary address only when Clerk says it was verified; anything else is None."""
    primary = account.get("primary_email_address_id")
    for entry in account.get("email_addresses") or []:
        if not isinstance(entry, dict) or entry.get("id") != primary:
            continue
        if (entry.get("verification") or {}).get("status") == "verified" and isinstance(entry.get("email_address"), str):
            return entry["email_address"].strip().lower()
    return None


def clerk_invited(account: dict) -> bool:
    if not settings.beta_invite_only:
        return True
    email = verified_primary_email(account)
    return email is not None and email in _entries(settings.beta_emails)


def require_invite(invited: bool) -> None:
    if not invited:
        raise HTTPException(403, NOT_INVITED)


def suspended(user_id: int) -> bool:
    """SUSPENDED_USER_IDS: users cut off from signing in and from every
    signed-in request, effective on their next request. Their sessions stop
    working without touching anyone else's."""
    return str(user_id) in _entries(settings.suspended_user_ids)


SUSPENDED = "This account is suspended."


def require_not_suspended(user_id: int) -> None:
    if suspended(user_id):
        raise HTTPException(403, SUSPENDED)
