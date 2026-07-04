# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #634 — the "Currently reading" badge never
surfaces on the new-UI (SPA) book page.

Reporter @alva-seal / @iroQuai (2026-07-03, on v4.1.5): the classic detail
page shows the sync-driven "Currently reading" marker (fork #509), but "New
UI does not show currently reading badge for me at all!"

Root cause: the SPA book page is fed by ``/api/v1/books/<id>``
(``cps.api.books.book_detail``), which returned only a flattened ``read``
boolean (true only for FINISHED). The in-progress tri-state
(``ub.ReadBook.read_status == STATUS_IN_PROGRESS``) that the classic page
renders off ``entry.read_status_raw == 2`` was never sent to the SPA, so the
badge could not render.

These tests pin the fix: a shared ``helper.book_is_in_progress`` derives the
flag with the SAME semantics as ``web.show_book`` (single source of truth,
guarding the read-state drift class of fork #579/#637), the detail serializer
emits ``in_progress``, and the endpoint wires the helper into the payload.
"""

import ast
import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOKS_PY = REPO_ROOT / "cps" / "api" / "books.py"


def _user(uid=1, authenticated=True, anonymous=False):
    return SimpleNamespace(id=uid, is_authenticated=authenticated, is_anonymous=anonymous)


# ── Pure derivation: built-in read_status tri-state (no custom read column) ──

@pytest.mark.unit
@pytest.mark.parametrize("read_status, expected", [
    (2, True),    # STATUS_IN_PROGRESS → currently reading
    (1, False),   # STATUS_FINISHED → read, not in-progress (must NOT regress #637)
    (0, False),   # STATUS_UNREAD
    (None, False),  # never synced → no ReadBook row (outer join miss)
])
def test_in_progress_builtin_read_status(read_status, expected):
    from cps.helper import book_is_in_progress
    # read_column_configured=False → derivation is pure, no DB touch
    assert book_is_in_progress(5, read_status, False, _user()) is expected


@pytest.mark.unit
def test_in_progress_false_for_anonymous_or_unauthenticated():
    from cps.helper import book_is_in_progress
    assert book_is_in_progress(5, 2, False, _user(authenticated=False)) is False
    assert book_is_in_progress(5, 2, False, _user(anonymous=True)) is False
    assert book_is_in_progress(5, 2, False, None) is False


# ── Custom read column: tri-state lives only in ub.ReadBook (fork #634 overlay) ──

@pytest.fixture
def ub_readbook_session(monkeypatch):
    import cps.ub as ub
    engine = create_engine("sqlite:///:memory:")
    ub.ReadBook.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    yield ub, session
    session.close()


@pytest.mark.unit
def test_in_progress_custom_column_reads_from_readbook_when_unread(ub_readbook_session):
    """With a custom read column, a falsy column value + an IN_PROGRESS ReadBook
    row must still surface the badge (the column can't express the tri-state)."""
    ub, session = ub_readbook_session
    from cps.helper import book_is_in_progress
    session.add(ub.ReadBook(user_id=1, book_id=5, read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    session.commit()
    # read_status_value is the (falsy) custom column value here, not the enum
    assert book_is_in_progress(5, False, True, _user(uid=1)) is True


@pytest.mark.unit
def test_in_progress_custom_column_finished_value_is_never_in_progress(ub_readbook_session):
    """A truthy custom-column value means finished — short-circuit to False even
    if a stale IN_PROGRESS ReadBook row exists."""
    ub, session = ub_readbook_session
    from cps.helper import book_is_in_progress
    session.add(ub.ReadBook(user_id=1, book_id=5, read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    session.commit()
    assert book_is_in_progress(5, True, True, _user(uid=1)) is False


@pytest.mark.unit
def test_in_progress_custom_column_no_readbook_row(ub_readbook_session):
    ub, session = ub_readbook_session
    from cps.helper import book_is_in_progress
    assert book_is_in_progress(5, False, True, _user(uid=1)) is False


@pytest.mark.unit
def test_in_progress_custom_column_finished_readbook_row_not_in_progress(ub_readbook_session):
    """A FINISHED ReadBook row must not be misread as in-progress."""
    ub, session = ub_readbook_session
    from cps.helper import book_is_in_progress
    session.add(ub.ReadBook(user_id=1, book_id=5, read_status=ub.ReadBook.STATUS_FINISHED))
    session.commit()
    assert book_is_in_progress(5, False, True, _user(uid=1)) is False


# ── Serializer emits the flag ──

@pytest.mark.unit
def test_serialize_book_detail_emits_in_progress():
    from cps.api.serializers import serialize_book_detail
    book = SimpleNamespace(
        id=5, title="X", series_index="1.0", has_cover=0, pubdate=None,
        authors=[], series=[], comments=[], tags=[], languages=[],
        publishers=[], identifiers=[], data=[],
    )
    assert serialize_book_detail(book, in_progress=True)["in_progress"] is True
    # Defaults to False so unread/finished books never falsely show the badge
    assert serialize_book_detail(book)["in_progress"] is False


# ── Endpoint wiring (source-pin — the pure tests can't reach the Flask view) ──

@pytest.mark.unit
def test_book_detail_endpoint_wires_helper_into_serializer():
    """book_detail must derive in_progress via the shared helper and pass it to
    serialize_book_detail — guards the wiring the unit tests can't exercise."""
    tree = ast.parse(BOOKS_PY.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "book_detail")
    src = ast.get_source_segment(BOOKS_PY.read_text(), fn)
    assert "book_is_in_progress(" in src, "book_detail must call the shared helper"
    assert "in_progress=in_progress" in src, "helper result must reach the serializer"
