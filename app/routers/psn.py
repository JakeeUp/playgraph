"""Link a PlayStation account by proving control of its public profile.

The player types their PSN Online ID, PlayGraph shows a one-time code, the
player puts it in their PSN "About Me", and PlayGraph reads that profile with
the one server-owned NPSSO. Players never give PlayGraph a PSN password,
cookie or token. See docs/PSN_INTEGRATION.md.

Security shape, mirroring app/routers/accounts.py:
- Every route needs the cookie session; get_current_user enforces Origin and
  the CSRF token on POST/DELETE, plus the per-user write budget.
- The pending code is bound to this browser session (state_key on the
  session ID), expires in 15 minutes and is consumed with GETDEL, so a stolen
  code, another browser or a replay cannot finish the link.
- The database allows one PSN account per user and one user per PSN account.
- When PSN_NPSSO is empty every route answers 404, before authentication.
"""
import json
import logging
import time

from arq.jobs import Job
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import psn_status
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import LinkedAccount, Platform, User, utcnow
from app.queue_codec import QUEUE_NAME, deserialize
from app.schemas import display_name
from app.security import rate_limit, redis_call, state_key
from app.services import psn

logger = logging.getLogger("playgraph.psn")
LINK_SECONDS = 900
VERIFICATION_METHOD = "psn_about_me"
JOB_PREFIX = "sync-psn-user-"
# Longest a link request waits for a slot in the Sony request budget before
# answering "busy". The worker has no such cap.
WEB_MAX_WAIT = 10.0


def require_psn_enabled():
    if not settings.psn_enabled:
        raise HTTPException(404, "PlayStation linking is not enabled")


router = APIRouter(tags=["playstation"], dependencies=[Depends(require_psn_enabled)])


class LinkStart(BaseModel):
    online_id: str = Field(min_length=1, max_length=32)


def job_id_for(user_id: int) -> str:
    return f"{JOB_PREFIX}{user_id}"


def _psn_link(db: Session, user_id: int) -> LinkedAccount | None:
    return db.query(LinkedAccount).filter_by(user_id=user_id, platform=Platform.psn).first()


def _pending_key(request: Request) -> str:
    return state_key("psn-link", request.state.session_id)


def _parse_pending(raw, user: User) -> dict | None:
    if not raw:
        return None
    try:
        pending = json.loads(raw)
        if pending.get("user_id") != user.id:
            return None
        return pending
    except (ValueError, TypeError, AttributeError):
        return None


async def _sony_unavailable_if_blocked(request: Request) -> None:
    if await psn_status.auth_blocked(getattr(request.app.state, "arq_pool", None)):
        raise HTTPException(503, "PlayStation is temporarily unavailable. Try again later.")


async def _sony_error(request: Request, exc: psn.PSNError, *, missing: str) -> HTTPException:
    """Map a PSN failure to a response a player can act on. Sony's own error
    text never reaches the browser."""
    if isinstance(exc, psn.PSNAuthError):
        await psn_status.block_auth(getattr(request.app.state, "arq_pool", None))
        logger.error("psn_operator_action_needed: Sony rejected the server PSN_NPSSO. Sign in as the server "
                     "PSN account, set a fresh PSN_NPSSO, then restart the API and worker.")
        return HTTPException(503, "PlayStation is temporarily unavailable. Try again later.")
    if isinstance(exc, psn.PSNRateLimitedError):
        logger.warning("psn_rate_limited")
        return HTTPException(503, "PlayStation is busy right now. Try again in a few minutes.",
                             headers={"Retry-After": str(max(1, int(exc.retry_after) + 1))})
    if isinstance(exc, psn.PSNNotFoundError):
        return HTTPException(404, missing)
    if isinstance(exc, psn.PSNPrivateError):
        return HTTPException(409, "PlayGraph can’t see that PSN profile. In your PlayStation privacy settings, "
                                  "let anyone see your profile, then try again.")
    logger.warning("psn_upstream_error status=%s", exc.status)
    return HTTPException(502, "PlayStation didn’t answer. Try again in a minute.")


@router.get("/auth/psn")
async def link_status(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """This account's PlayStation link, and any code waiting in this browser."""
    linked = _psn_link(db, user.id)
    pending = None
    if linked is None:
        waiting = _parse_pending(await redis_call(request, "get", _pending_key(request)), user)
        if waiting:
            pending = {"online_id": waiting["online_id"], "code": waiting["code"],
                       "expires_in": max(0, int(waiting.get("expires_at", 0) - time.time()))}
    store = getattr(request.app.state, "arq_pool", None)
    return {
        "enabled": True,
        "linked": linked is not None,
        "online_id": linked.display_handle if linked else None,
        "verified_at": linked.verified_at if linked else None,
        "last_synced_at": linked.last_synced_at if linked else None,
        "job_id": job_id_for(user.id) if linked else None,
        "sync": await psn_status.read_status(store, user.id) if linked else None,
        "pending": pending,
    }


@router.post("/auth/psn/link")
async def start_link(body: LinkStart, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Look up the Online ID and issue a one-time code for its About Me."""
    if _psn_link(db, user.id):
        raise HTTPException(409, "A PlayStation account is already linked to this PlayGraph account")
    try:
        online_id = psn.validate_online_id(body.online_id)
    except ValueError:
        raise HTTPException(422, "Enter a PSN Online ID: 3 to 16 letters, numbers, hyphens or underscores, "
                                 "starting with a letter.") from None
    await rate_limit(request, "psn-link", str(user.id), 5, 600)
    await _sony_unavailable_if_blocked(request)
    try:
        async with psn.PSNClient(max_wait=WEB_MAX_WAIT) as client:
            profile = await client.resolve_profile(online_id)
    except psn.PSNError as exc:
        raise await _sony_error(request, exc, missing="No PSN account has that Online ID.") from None
    if db.query(LinkedAccount).filter_by(platform=Platform.psn, platform_user_id=profile["account_id"]).first():
        raise HTTPException(409, "That PSN account is already linked to a PlayGraph account.")
    code = psn.generate_verification_code()
    pending = {"user_id": user.id, "account_id": profile["account_id"], "online_id": profile["online_id"],
               "code": code, "expires_at": int(time.time()) + LINK_SECONDS}
    await redis_call(request, "set", _pending_key(request), json.dumps(pending), ex=LINK_SECONDS)
    return {"online_id": profile["online_id"], "code": code, "expires_in": LINK_SECONDS}


@router.delete("/auth/psn/link", status_code=204)
async def cancel_link(request: Request, user: User = Depends(get_current_user)):
    await redis_call(request, "delete", _pending_key(request))
    return Response(status_code=204)


@router.post("/auth/psn/link/check")
async def check_link(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Read the profile's About Me; link the account if this browser's code is there."""
    # Each check is two Sony requests made as the owner's account; keep it tight.
    await rate_limit(request, "psn-check", str(user.id), 10, 600)
    if _psn_link(db, user.id):
        raise HTTPException(409, "A PlayStation account is already linked to this PlayGraph account")
    key = _pending_key(request)
    pending = _parse_pending(await redis_call(request, "get", key), user)
    if pending is None:
        raise HTTPException(409, "No PlayStation link is waiting in this browser, or the code expired. Start again.")
    await _sony_unavailable_if_blocked(request)
    try:
        async with psn.PSNClient(max_wait=WEB_MAX_WAIT) as client:
            found = await client.check_verification(pending["account_id"], pending["code"])
    except psn.PSNError as exc:
        raise await _sony_error(request, exc, missing="That PSN account could not be found. Start again.") from None
    if not found:
        raise HTTPException(409, "Code not found in your About Me yet. Save it on PSN, wait a minute, and try again.")
    # The Sony call awaited I/O. Recheck that this session is still live, then
    # consume the code: GETDEL lets exactly one request finish the link.
    if await redis_call(request, "get", state_key("session", request.state.session_id)) not in (
            str(user.id), str(user.id).encode()):
        raise HTTPException(401, "Session expired or revoked")
    consumed = _parse_pending(await redis_call(request, "getdel", key), user)
    if consumed is None or (consumed["account_id"], consumed["code"]) != (pending["account_id"], pending["code"]):
        raise HTTPException(409, "This code expired or was already used. Start again.")
    if db.query(LinkedAccount).filter_by(platform=Platform.psn, platform_user_id=consumed["account_id"]).first():
        raise HTTPException(409, "That PSN account is already linked to a PlayGraph account.")
    try:
        db.add(LinkedAccount(user_id=user.id, platform=Platform.psn, platform_user_id=consumed["account_id"],
                             display_handle=display_name(consumed["online_id"], "", limit=32) or None,
                             verified_at=utcnow(), verification_method=VERIFICATION_METHOD))
        db.commit()
    except IntegrityError:
        # Another tab or account won the race; the unique constraints refused this one.
        db.rollback()
        raise HTTPException(409, "A PlayStation account was linked elsewhere first. Refresh and check.") from None
    logger.info("psn_linked user_id=%s", user.id)
    job_id = None
    try:
        job = await request.app.state.arq_pool.enqueue_job("sync_psn_library", user.id, _job_id=job_id_for(user.id))
        job_id = job.job_id if job else job_id_for(user.id)
    except (RedisError, OSError, AttributeError):
        pass  # linked either way; the account page offers Sync now
    return {"linked": True, "online_id": consumed["online_id"], "job_id": job_id}


@router.post("/me/psn/sync", status_code=202)
async def sync_psn(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Queue a PlayStation import. Same job-id and status pattern as the Steam sync."""
    if _psn_link(db, user.id) is None:
        raise HTTPException(400, "No linked PlayStation account")
    await rate_limit(request, "psn-sync", str(user.id), 3, 3600)
    await _sony_unavailable_if_blocked(request)
    try:
        job = await request.app.state.arq_pool.enqueue_job("sync_psn_library", user.id, _job_id=job_id_for(user.id))
    except RedisError:
        raise HTTPException(503, "Sync service unavailable") from None
    if job is None:
        raise HTTPException(409, "A PlayStation sync for this account is already in progress or finished "
                                 "very recently. Try again in a few minutes.")
    return {"job_id": job.job_id, "status": "queued"}


@router.get("/me/psn/sync/status/{job_id}")
async def sync_psn_status(job_id: str, request: Request, user: User = Depends(get_current_user)):
    if job_id != job_id_for(user.id):
        raise HTTPException(404, "Sync job not found")
    job = Job(job_id, request.app.state.arq_pool, _queue_name=QUEUE_NAME, _deserializer=deserialize)
    try:
        status = await job.status()
        result = None
        if status.name == "complete":
            info = await job.result_info()
            if info is None or not info.success:
                return {"job_id": job_id, "status": "failed", "result": None}
            data = info.result if isinstance(info.result, dict) else {}
            result = {k: data[k] for k in ("games_synced",) if type(data.get(k)) is int}
            result.update({k: data[k] for k in ("trophies_visible", "playtime_visible") if type(data.get(k)) is bool})
        return {"job_id": job_id, "status": status.name, "result": result}
    except RedisError:
        raise HTTPException(503, "Sync status unavailable") from None
