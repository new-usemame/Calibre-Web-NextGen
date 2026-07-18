# SPDX-License-Identifier: GPL-3.0-or-later
"""janeczku/calibre-web#3670: saving a book with tags that differ only by
letter case (e.g. ``Java, java``) must not raise a database integrity error.

``tags.name`` uses a NOCASE-unique collation and ``books_tags_link`` has a
composite ``(book, tag)`` primary key, so two case-variant tag strings resolve
to the same row. A book saved with both variants must collapse to a single tag
and a single link rather than crashing with
``UNIQUE constraint failed: tags.name`` (or ...books_tags_link...).

Verified on this fork (2026-07-18): the crash does NOT reproduce here. The
load-bearing defense is the calibre session's ``autoflush=True`` (see
``cps/db.py`` ``session_factory``): when ``add_objects`` iterates the second
case-variant, the autoflush'd lookup sees the tag created for the first variant
and - because the ``name`` column is ``COLLATE NOCASE`` - resolves both to the
same row. Disable autoflush and the same payload crashes exactly as reported
upstream (proven by ``test_autoflush_off_reproduces_upstream_crash`` below).

These tests pin both the user-visible behavior (no crash) and the invariant that
protects it (autoflush stays on), so a future session-config or ``add_objects``
refactor can't silently reintroduce the upstream crash.
"""
import inspect
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cps import db
from cps import editbooks


def _engine():
    engine = create_engine("sqlite://")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute("ATTACH DATABASE ':memory:' AS calibre"),
    )
    db.Base.metadata.create_all(engine)
    return engine


def _session(autoflush=True):
    return sessionmaker(bind=_engine(), autoflush=autoflush)()


def _make_book(session, title="Case Test"):
    now = datetime.now(timezone.utc)
    book = db.Books(title, title, "Author", now, now, "1.0", now, f"{title}-path", 1, [], [])
    session.add(book)
    session.commit()
    return book


@pytest.mark.unit
def test_control_constraints_bite_when_variants_forced_distinct():
    """Sanity: the in-memory schema really enforces the crash conditions.

    Inserting two distinct case-variant tag rows must fail on the NOCASE-unique
    ``tags.name`` constraint, proving the repro exercises the same constraints as
    a real Calibre metadata.db.
    """
    session = _session()
    try:
        _make_book(session)
        session.add(db.Tags("Java"))
        session.commit()
        session.add(db.Tags("java"))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


@pytest.mark.unit
def test_modify_database_object_tags_case_variants_no_crash():
    """The exact #3670 payload (``Java, java``) through the real code path.

    ``edit_book_tags`` splits + ``helper.uniq``-dedups (which keeps both case
    variants) then calls ``modify_database_object``. The fork must collapse the
    variants to one tag + one link and commit without error.
    """
    session = _session()
    try:
        book = _make_book(session)
        changed = editbooks.modify_database_object(
            ["Java", "java"], book.tags, db.Tags, session, "tags"
        )
        session.commit()  # must NOT raise UNIQUE constraint failed
        assert changed is True
        assert session.query(db.Tags).count() == 1
        assert len(book.tags) == 1
        assert book.tags[0].name in ("Java", "java")
    finally:
        session.rollback()
        session.close()


@pytest.mark.unit
def test_modify_database_object_tags_case_variants_with_preexisting_tag():
    """Same collision when the lower-case variant already exists globally."""
    session = _session()
    try:
        book = _make_book(session)
        session.add(db.Tags("java"))  # pre-existing, unlinked
        session.commit()
        editbooks.modify_database_object(
            ["Java", "java"], book.tags, db.Tags, session, "tags"
        )
        session.commit()
        assert session.query(db.Tags).count() == 1
        assert len(book.tags) == 1
    finally:
        session.rollback()
        session.close()


@pytest.mark.unit
def test_autoflush_off_reproduces_upstream_crash():
    """Characterize the load-bearing invariant: without autoflush the fork
    crashes exactly as janeczku#3670 reports. This documents *why* the calibre
    session must keep ``autoflush=True`` and gives the pin its teeth - flip it
    off and this expectation, plus the source-pin below, fail loudly.
    """
    session = _session(autoflush=False)
    try:
        book = _make_book(session)
        with pytest.raises(IntegrityError) as exc:
            editbooks.modify_database_object(
                ["Java", "java"], book.tags, db.Tags, session, "tags"
            )
            session.commit()
        assert "UNIQUE constraint failed" in str(exc.value.orig)
    finally:
        session.rollback()
        session.close()


@pytest.mark.unit
def test_calibre_session_configures_autoflush_on():
    """Source-pin the invariant that protects the behavior: the calibre session
    factory must be built with ``autoflush=True``. If a refactor drops it, the
    #3670 crash returns for real users - catch it here, not in production.
    """
    src = inspect.getsource(db.CalibreDB.setup_db)
    assert "autoflush=True" in src, (
        "calibre session_factory must keep autoflush=True; without it, saving a "
        "book with case-variant tags (Java, java) crashes with a UNIQUE "
        "constraint error (janeczku/calibre-web#3670)."
    )
