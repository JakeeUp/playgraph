"""POST /demo/login: a normal session for the one seeded demo player."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.demo import DEMO_STEAM_ID
from app.models import LinkedAccount, Platform
from app.routers.accounts import browser_cookie
from app.security import issue_session, rate_limit

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/login", status_code=204)
async def demo_login(request: Request, db: Session = Depends(get_db)):
    if not settings.demo_mode:
        raise HTTPException(404, "Not found")
    # Same browser checks as the other sign-in posts.
    if request.headers.get("origin") != settings.app_base_url or request.headers.get("x-playgraph-auth") != "1":
        raise HTTPException(403, "Start sign-in on PlayGraph in this browser")
    await rate_limit(request, "demo-login", request.client.host if request.client else "unknown", 10, 60)
    link = db.query(LinkedAccount).filter_by(platform=Platform.steam, platform_user_id=DEMO_STEAM_ID).first()
    if link is None:
        raise HTTPException(503, "The demo data has not been loaded yet")
    return browser_cookie(await issue_session(request, link.user_id))
