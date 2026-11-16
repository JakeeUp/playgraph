"""Who a viewer can see. Shared by the feeds, review lists and profiles.

A block hides both people from each other: the blocker stops seeing the
blocked player's reviews and comments, and the blocked player stops seeing
the blocker's. Nothing ever says a block exists; hidden things look the same
as things that are not there.
"""
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.cache import REVIEWS, cached
from app.models import Block, Comment, Follow, Review, User


def reposter_reachable(viewer_id: int):
    """SQL filter: this viewer may see a repost from User (the reposter).

    Public profiles are open. Followers-only profiles reach accepted
    followers. A private profile reaches nobody else. Blocks are a separate
    filter (not_hidden) applied by the caller. `User` must be joined.
    """
    return or_(
        User.profile_visibility == "public",
        and_(
            User.profile_visibility == "followers",
            exists().where(and_(
                Follow.follower_id == viewer_id,
                Follow.followee_id == User.id,
                Follow.status == "accepted",
            )),
        ),
        User.id == viewer_id,
    )


def has_blocks(db: Session, viewer: User | None) -> bool:
    """Whether any block involves this viewer, in either direction.

    Public lists are cached for everyone; only a viewer with blocks needs
    their own uncached, filtered copy.
    """
    if viewer is None:
        return False
    return db.query(Block.id).filter(or_(Block.blocker_id == viewer.id, Block.blocked_id == viewer.id)).first() is not None


def not_hidden(column, viewer_id: int):
    """A filter keeping rows whose author `column` is not blocked either way with the viewer."""
    return and_(column.not_in(select(Block.blocked_id).where(Block.blocker_id == viewer_id)),
                column.not_in(select(Block.blocker_id).where(Block.blocked_id == viewer_id)))


def blocked_either_way(db: Session, first: int, second: int) -> bool:
    return db.query(Block.id).filter(or_(and_(Block.blocker_id == first, Block.blocked_id == second),
                                         and_(Block.blocker_id == second, Block.blocked_id == first))).first() is not None


def blocked_by(db: Session, blocker: int, blocked: int) -> bool:
    return db.query(Block.id).filter_by(blocker_id=blocker, blocked_id=blocked).first() is not None


def follows(db: Session, follower: int, followee: int) -> bool:
    """An accepted follow. A pending request grants nothing."""
    return db.query(Follow.id).filter_by(follower_id=follower, followee_id=followee, status="accepted").first() is not None


def follow_status(db: Session, follower: int, followee: int) -> str | None:
    """'accepted', 'pending' or None."""
    return db.query(Follow.status).filter_by(follower_id=follower, followee_id=followee).scalar()


def is_public_participant(db: Session, user_id: int) -> bool:
    """Whether this player's ID and name are already public somewhere.

    Anyone who has posted a review or comment, or follows someone, already
    shows up on public pages. A player who has done none of that stays
    unfindable by guessing IDs: their profile answers 404 like a missing one.
    """
    return db.query(or_(exists().where(Review.user_id == user_id),
                        exists().where(Comment.user_id == user_id),
                        exists().where(Follow.follower_id == user_id, Follow.status == "accepted"))).scalar()


async def for_viewer(request, db: Session, viewer: User | None, parts: tuple, compute):
    """A public response, as this viewer may see it.

    compute(viewer_id) builds the response, filtering out blocked authors when
    viewer_id is set. Guests and viewers without any blocks share the normal
    cached copy (compute(None)); a viewer with blocks gets an uncached copy of
    their own, so one person's blocks never leak into what others are served.
    """
    if has_blocks(db, viewer):
        return jsonable_encoder(await run_in_threadpool(compute, viewer.id))
    return await cached(request, REVIEWS, parts, 30, lambda: compute(None))
