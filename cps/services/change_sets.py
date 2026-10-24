# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Change sets: one reviewed batch of metadata changes (preview, apply, undo).

A change set is a list of per-book, per-field rows with the value seen at
preview time and the value to write. Apply walks the rows, writes only where
the book still holds the previewed value, and records each row's outcome, so
a bulk run reports per-book failures (#1195, #1703) instead of one success.
Undo reverses an applied set the same way. Rows live in app.db, keyed by the
Calibre book id; metadata.db is never extended.

The session and Flask-bound helpers come in as arguments, so this module is
testable against a plain SQLite session.
"""
from datetime import datetime, timezone

from sqlalchemy import func

from .. import db, ub
from .fields import FIELD_SPECS
from .value_map import fold


def current_values(book, field):
    return [row.name for row in getattr(book, FIELD_SPECS[field].link_attr)]


def _row_for(calibre_session, field, name):
    """The row for ``name``. calibre's names are unique case-insensitively
    (NOCASE), so an existing row that differs only in case is renamed."""
    spec = FIELD_SPECS[field]
    row = calibre_session.query(spec.model).filter(func.lower(spec.model.name) == name.lower()).first()
    if row is None:
        row = spec.new_row(name)
        calibre_session.add(row)
    elif row.name != name:
        row.name = name
        if spec.has_sort:
            row.sort = name
    return row


def _write(calibre_session, book, field, values):
    setattr(book, FIELD_SPECS[field].link_attr, [_row_for(calibre_session, field, v) for v in values])


def create(app_session, source, user_id, changes):
    """Store ``changes`` ({book_id, field, old, new}) as a proposed set."""
    change_set = ub.MetadataChangeSet(source=source, created_by=user_id, status="proposed")
    app_session.add(change_set)
    app_session.flush()
    for change in changes:
        app_session.add(ub.MetadataChange(
            set_id=change_set.id, book_id=change["book_id"], field=change["field"],
            old_value=change["old"], new_value=change["new"], status="proposed"))
    app_session.commit()
    return change_set


def rows(app_session, set_id):
    return (app_session.query(ub.MetadataChange)
            .filter(ub.MetadataChange.set_id == set_id)
            .order_by(ub.MetadataChange.book_id, ub.MetadataChange.id).all())


def _walk(app_session, calibre_session, set_id, *, eligible, expect, target, done_status,
          exclude, mark_modified, queue_embed):
    """Shared walk for apply and undo: one calibre commit per book."""
    by_book = {}
    for row in rows(app_session, set_id):
        if row.status == eligible:
            by_book.setdefault(row.book_id, []).append(row)
    written, touched = [], {}
    for book_id, book_rows in by_book.items():
        if book_id in exclude:
            for row in book_rows:
                row.status, row.error = "skipped", "left out at apply"
            continue
        book = calibre_session.get(db.Books, book_id)
        if book is None:
            for row in book_rows:
                row.status, row.error = "skipped", "book no longer in the library"
            continue
        # Compared folded: calibre treats names that differ only in case as one
        # value, and an earlier book in this same set may have re-cased a row
        # this book shares.
        stale = [r for r in book_rows
                 if [fold(v) for v in current_values(book, r.field)] != [fold(v) for v in expect(r)]]
        if stale:
            for row in book_rows:
                row.status, row.error = "skipped", "changed since the preview"
            continue
        try:
            for row in book_rows:
                touched.setdefault(row.field, set()).update(expect(row))
                _write(calibre_session, book, row.field, target(row))
            mark_modified(book)
            calibre_session.commit()
        except Exception as error:  # reported per book; the walk goes on
            calibre_session.rollback()
            for row in book_rows:
                row.status, row.error = "failed", str(error)
            continue
        for row in book_rows:
            row.status, row.error = done_status, None
        written.append((book, {r.field: ", ".join(target(r)) for r in book_rows}))

    removed = []
    for field, names in touched.items():
        model = FIELD_SPECS[field].model
        for name in sorted(names):
            row = calibre_session.query(model).filter(model.name == name).first()
            if row is not None and not row.books:
                calibre_session.delete(row)
                removed.append(name)
    if removed:
        calibre_session.commit()
    app_session.commit()
    # Queue the file embed only after the metadata.db commits (#707).
    for book, changed in written:
        queue_embed(book, changed)
    return {"written": len(written), "removed_values": removed}


def apply(app_session, calibre_session, set_id, *, exclude=(), mark_modified, queue_embed):
    change_set = app_session.get(ub.MetadataChangeSet, set_id)
    if change_set is None or change_set.status != "proposed":
        raise ValueError("only a proposed change set can be applied")
    result = _walk(app_session, calibre_session, set_id, eligible="proposed",
                   expect=lambda r: list(r.old_value or []), target=lambda r: list(r.new_value or []),
                   done_status="applied", exclude=set(exclude),
                   mark_modified=mark_modified, queue_embed=queue_embed)
    change_set.status, change_set.applied_at = "applied", datetime.now(timezone.utc)
    app_session.commit()
    return result


def undo(app_session, calibre_session, set_id, *, mark_modified, queue_embed):
    change_set = app_session.get(ub.MetadataChangeSet, set_id)
    if change_set is None or change_set.status != "applied":
        raise ValueError("only an applied change set can be undone")
    result = _walk(app_session, calibre_session, set_id, eligible="applied",
                   expect=lambda r: list(r.new_value or []), target=lambda r: list(r.old_value or []),
                   done_status="undone", exclude=set(),
                   mark_modified=mark_modified, queue_embed=queue_embed)
    change_set.status = "undone"
    app_session.commit()
    return result


def summary(app_session, calibre_session, change_set):
    """The set and its rows, with titles, for the UI."""
    out = []
    for row in rows(app_session, change_set.id):
        book = calibre_session.get(db.Books, row.book_id)
        out.append({
            "id": row.id, "book_id": row.book_id, "field": row.field,
            "title": book.title if book else None,
            "authors": [a.name for a in book.authors] if book else [],
            "before": row.old_value or [], "after": row.new_value or [],
            "status": row.status, "error": row.error,
        })
    counts = {}
    for item in out:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "id": change_set.id, "source": change_set.source, "status": change_set.status,
        "created_at": change_set.created_at.isoformat() if change_set.created_at else None,
        "applied_at": change_set.applied_at.isoformat() if change_set.applied_at else None,
        "counts": counts, "changes": out,
    }
