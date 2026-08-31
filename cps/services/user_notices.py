# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic, occurrence-scoped user notices.

Consumers supply an opaque notice type and JSON payload. This core intentionally
contains no device, protocol, or feature-specific policy.
"""

from datetime import datetime, timezone

from .. import ub


def create_notice_event(session, *, notice_type, occurrence_key, scope,
                        audience_user_ids, payload, book_id=None, book_uuid=None,
                        title_snapshot=None):
    """Create one event and its audience, or no event when the audience is empty."""
    audience = sorted({int(user_id) for user_id in audience_user_ids})
    existing = session.query(ub.NoticeEvent).filter(
        ub.NoticeEvent.notice_type == notice_type,
        ub.NoticeEvent.occurrence_key == occurrence_key,
    ).one_or_none()
    if existing is not None:
        delivered = {row.user_id for row in existing.deliveries}
        for user_id in audience:
            if user_id not in delivered:
                existing.deliveries.append(ub.UserNoticeDelivery(user_id=user_id))
        session.commit()
        return existing
    if not audience:
        return None
    event = ub.NoticeEvent(
        notice_type=notice_type,
        occurrence_key=occurrence_key,
        scope=scope,
        book_id=book_id,
        book_uuid=book_uuid,
        title_snapshot=title_snapshot,
        payload_json=dict(payload or {}),
        active=True,
    )
    session.add(event)
    session.flush()
    for user_id in audience:
        event.deliveries.append(ub.UserNoticeDelivery(user_id=user_id))
    session.commit()
    return event


def list_active_notices(session, *, user_id, book_id=None, notice_type=None,
                        mark_presented=False):
    query = (
        session.query(ub.NoticeEvent)
        .join(ub.UserNoticeDelivery)
        .filter(
            ub.UserNoticeDelivery.user_id == user_id,
            ub.UserNoticeDelivery.dismissed_at.is_(None),
            ub.NoticeEvent.active.is_(True),
        )
    )
    if book_id is not None:
        query = query.filter(
            ub.NoticeEvent.scope == "book", ub.NoticeEvent.book_id == book_id,
        )
    if notice_type is not None:
        query = query.filter(ub.NoticeEvent.notice_type == notice_type)
    events = query.order_by(ub.NoticeEvent.created_at.asc(), ub.NoticeEvent.id.asc()).all()
    if mark_presented and events:
        now = datetime.now(timezone.utc)
        event_ids = [event.id for event in events]
        session.query(ub.UserNoticeDelivery).filter(
            ub.UserNoticeDelivery.user_id == user_id,
            ub.UserNoticeDelivery.event_id.in_(event_ids),
            ub.UserNoticeDelivery.first_presented_at.is_(None),
        ).update({ub.UserNoticeDelivery.first_presented_at: now}, synchronize_session=False)
        session.commit()
    return events


def dismiss_notices(session, *, user_id, notice_ids):
    """Permanently dismiss exactly the addressed occurrences for one user."""
    ids = sorted({int(notice_id) for notice_id in notice_ids})
    if not ids:
        return 0
    changed = session.query(ub.UserNoticeDelivery).filter(
        ub.UserNoticeDelivery.user_id == user_id,
        ub.UserNoticeDelivery.event_id.in_(ids),
        ub.UserNoticeDelivery.dismissed_at.is_(None),
    ).update(
        {ub.UserNoticeDelivery.dismissed_at: datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    session.commit()
    return changed


def dismiss_notice(session, *, user_id, notice_id):
    return dismiss_notices(session, user_id=user_id, notice_ids=[notice_id])


def serialize_notice(event):
    return {
        "id": event.id,
        "type": event.notice_type,
        "scope": event.scope,
        "occurred_at": event.created_at.isoformat() if event.created_at else None,
        "book": ({
            "id": event.book_id,
            "uuid": event.book_uuid,
            "title": event.title_snapshot,
        } if event.scope == "book" else None),
        "payload": event.payload_json or {},
    }
