# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-account Discover scope, shared by the SPA, Classic and OPDS."""
import re

from flask import g, has_request_context
from sqlalchemy import Column, Integer, MetaData, Table, and_, false, or_, select, true

from .. import db, logger, magic_shelf, ub

log = logger.create()

_APP_BOOK_SHELF_LINK = Table(
    "book_shelf_link", MetaData(),
    Column("book_id", Integer),
    Column("shelf", Integer),
    schema="app_settings",
)


def saved_source(user):
    if not user.is_authenticated or user.is_anonymous:
        return "library"
    value = user.get_view_property("discover", "source")
    if value is None:
        return "library"
    return value if isinstance(value, str) else "unavailable"


def parse_source(value):
    if value == "library":
        return "library", None
    if not isinstance(value, str):
        raise ValueError("Choose an available Discover source")
    match = re.fullmatch(r"(shelf|smart):([1-9][0-9]{0,18})", value)
    if not match or int(match[2]) > 2**63 - 1:
        raise ValueError("Choose an available Discover source")
    return match[1], int(match[2])


def _source_record(kind, source_id, user):
    model = ub.Shelf if kind == "shelf" else ub.MagicShelf
    record = ub.session.query(model).filter(model.id == source_id).first()
    uid = int(user.id) if user.is_authenticated and not user.is_anonymous else None
    if record is None or not (record.is_public or (uid is not None and record.user_id == uid)):
        return None
    if kind == "smart" and uid is not None:
        # Hidden public smart shelves are not offered by the existing shelf
        # picker/sidebar. Treat a saved one the same as any unavailable source.
        visible_ids = {
            shelf.id for shelf in magic_shelf.get_visible_magic_shelves_for_user(uid)
        }
        if record.id not in visible_ids:
            return None
    return record


def _record_filter(kind, record, user):
    if kind == "shelf":
        # This SQL runs against Calibre's attached app_settings DB. Avoid
        # materializing a large shelf into Python or a giant IN parameter list.
        return db.Books.id.in_(
            select(_APP_BOOK_SHELF_LINK.c.book_id)
            .where(_APP_BOOK_SHELF_LINK.c.shelf == record.id)
        )
    try:
        return _strict_smart_shelf_filter(record.rules, user_id=int(user.id))
    except Exception:
        log.exception("Could not build Discover source smart shelf %s", record.id)
        return None


def _strict_smart_shelf_filter(rules, *, user_id):
    """Build a smart-shelf predicate only when every saved rule is usable.

    ``magic_shelf.build_query_from_rules`` intentionally skips rules it cannot
    compile. That is convenient for legacy shelves, but unsafe for a Discover
    source: dropping one bad branch can turn a narrow source into a broad one.
    Validate the complete tree with the same leaf builder, then combine those
    predicates without ignoring any malformed or unavailable rule.
    """
    if not isinstance(rules, dict):
        return None
    condition = rules.get("condition", "AND")
    children = rules.get("rules")
    if (not isinstance(condition, str) or condition.upper() not in {"AND", "OR"}
            or not isinstance(children, list) or not children):
        return None

    filters = []
    for child in children:
        if not isinstance(child, dict):
            return None
        if "condition" in child:
            predicate = _strict_smart_shelf_filter(child, user_id=user_id)
        else:
            try:
                predicate = magic_shelf.build_filter_from_rule(child, user_id=user_id)
            except Exception:
                log.exception("Could not build Discover source smart-shelf rule")
                return None
        if predicate is None:
            return None
        filters.append(predicate)

    return and_(*filters) if condition.upper() == "AND" else or_(*filters)


def filter_for(user):
    """Return an additional book filter and whether the saved source is usable.

    A deleted, private or malformed source must never become the whole library.
    Callers combine this with their existing book visibility and read filters.
    """
    try:
        source = saved_source(user)
        cache = None
        if has_request_context():
            cache = getattr(g, "_discover_source_filter_cache", None)
            if cache is None:
                cache = g._discover_source_filter_cache = {}
        key = (int(user.id), source) if user.is_authenticated and not user.is_anonymous else (None, source)
        if cache is not None and key in cache:
            return cache[key]
        kind, source_id = parse_source(source)
    except ValueError:
        return false(), False
    if kind == "library":
        result = (true(), True)
    else:
        record = _source_record(kind, source_id, user)
        if record is None:
            result = (false(), False)
        else:
            predicate = _record_filter(kind, record, user)
            result = (false(), False) if predicate is None else (predicate, True)
    if cache is not None:
        cache[key] = result
    return result


def clear_filter_cache():
    """Invalidate predicates after a preference write in the same request."""
    if has_request_context():
        g._discover_source_filter_cache = {}


def validate_source(value, user):
    kind, source_id = parse_source(value)
    if kind != "library":
        record = _source_record(kind, source_id, user)
        if record is None or _record_filter(kind, record, user) is None:
            raise ValueError("Choose an available Discover source")
    return value


def settings_payload(user):
    uid = int(user.id)
    options = [{"value": "library", "name": "", "kind": "library"}]
    records = ub.session.query(ub.Shelf).filter(
        or_(ub.Shelf.user_id == uid, ub.Shelf.is_public == 1)
    ).all()
    records.sort(key=lambda record: ((record.name or "").casefold(), record.id))
    for record in records:
        options.append({"value": f"shelf:{record.id}", "name": record.name or "", "kind": "shelf"})

    smart_records = magic_shelf.get_visible_magic_shelves_for_user(uid)
    smart_records.sort(key=lambda record: ((record.name or "").casefold(), record.id))
    for record in smart_records:
        name = magic_shelf.system_magic_shelf_display_name(record)
        options.append({"value": f"smart:{record.id}", "name": name or "", "kind": "smart"})
    _filter, available = filter_for(user)
    return {"source": saved_source(user), "available": available, "sources": options}
