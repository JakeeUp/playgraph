"""Notifications belong to one recipient, never a public feed or cache."""
from datetime import timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models import Notification, User, utcnow
from app.visibility import blocked_either_way, not_hidden, reposter_reachable

RETENTION_DAYS = 90


def notify(db: Session, recipient: int, actor: int, kind: str,
           review_id: int | None = None, comment_id: int | None = None):
    # Keep the event in the action's transaction, so a failed action cannot ring the bell.
    if recipient != actor and not blocked_either_way(db, recipient, actor):
        db.add(Notification(recipient_id=recipient, actor_id=actor, kind=kind,
                            review_id=review_id, comment_id=comment_id))


def visible(db: Session, recipient: int):
    return (db.query(Notification).join(User, User.id == Notification.actor_id)
            .filter(Notification.recipient_id == recipient,
                    Notification.created_at >= utcnow() - timedelta(days=RETENTION_DAYS),
                    not_hidden(Notification.actor_id, recipient),
                    or_(Notification.kind.not_in(("repost", "quote")), reposter_reachable(recipient))))


def unread_count(db: Session, recipient: int) -> int:
    return visible(db, recipient).filter(Notification.read_at.is_(None)).count()


def inbox(db: Session, recipient: int, limit: int, offset: int) -> dict:
    query = visible(db, recipient)
    latest = query.with_entities(func.max(Notification.id)).scalar() or 0
    query = query.filter(Notification.id <= latest)
    day = func.date(Notification.created_at)
    grouped = query.with_entities(
        Notification.kind, Notification.review_id, Notification.comment_id, day.label("day"),
        func.max(Notification.id).label("id"), func.max(Notification.created_at).label("created_at"),
        func.count(func.distinct(Notification.actor_id)).label("actor_count"),
        func.count(Notification.id).filter(Notification.read_at.is_(None)).label("unread_count"),
    ).group_by(Notification.kind, Notification.review_id, Notification.comment_id, day)
    rows = grouped.order_by(func.max(Notification.id).desc()).offset(offset).limit(limit).all()
    items = []
    for row in rows:
        # Only a few names are needed, even when thousands of people liked a review.
        actors = (query.filter(Notification.kind == row.kind, Notification.review_id == row.review_id,
                               Notification.comment_id == row.comment_id, day == row.day)
                  .with_entities(User.id, User.display_name, User.handle)
                  .group_by(User.id, User.display_name, User.handle)
                  .order_by(func.max(Notification.id).desc()).limit(3).all())
        items.append({"id": row.id, "kind": row.kind, "review_id": row.review_id, "comment_id": row.comment_id,
                      "created_at": row.created_at, "actor_count": row.actor_count,
                      "actors": [dict(actor._mapping) for actor in actors], "unread_count": row.unread_count})
    return {"items": items, "total": grouped.count(), "unread_count": unread_count(db, recipient),
            "latest_id": latest}


def remove_between(db: Session, first: int, second: int):
    db.query(Notification).filter(or_(
        and_(Notification.recipient_id == first, Notification.actor_id == second),
        and_(Notification.recipient_id == second, Notification.actor_id == first),
    )).delete(synchronize_session=False)


def prune(db: Session) -> int:
    return db.query(Notification).filter(
        Notification.created_at < utcnow() - timedelta(days=RETENTION_DAYS)
    ).delete(synchronize_session=False)
