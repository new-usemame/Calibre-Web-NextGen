# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Issue #1190: "No book found for checksum" must say why it missed.

One log line used to cover three causes that need different answers: a
checksum row whose book id is gone, nothing registered at all, and a
checksum the server never registered. The extra detail is rate-limited per
checksum because a stuck device polls repeatedly.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cps
from cps.progress_syncing.models import BookFormatChecksum

CHECKSUM = "9e107d9d372bb6826bd81d3542a419d6"


def _kosync():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    BookFormatChecksum.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    getattr(_kosync(), "_miss_diag_last", {}).clear()
    yield
    getattr(_kosync(), "_miss_diag_last", {}).clear()


def _describe(session, checksum=CHECKSUM):
    ns = SimpleNamespace(session=session)
    with patch.object(cps, "calibre_db", ns, create=True):
        return _kosync()._describe_checksum_miss(checksum)


@pytest.mark.unit
class TestChecksumMissDiagnostics:
    def test_nothing_registered_names_backfill(self, session):
        msg = _describe(session)
        assert "no checksums are registered" in msg
        assert "backfill" in msg

    def test_other_checksums_registered_names_unserved_copy(self, session):
        session.add(BookFormatChecksum(1, "EPUB", "a" * 32))
        session.add(BookFormatChecksum(2, "EPUB", "b" * 32))
        session.commit()
        msg = _describe(session)
        assert "2 checksums are registered" in msg
        assert "never served" in msg

    def test_row_for_missing_book_names_stale_id(self, session):
        session.add(BookFormatChecksum(42, "EPUB", CHECKSUM))
        session.commit()
        msg = _describe(session)
        assert "book id 42" in msg
        assert "stale" in msg

    def test_detail_is_rate_limited_per_checksum(self, session):
        assert _describe(session) != ""
        assert _describe(session) == ""
        assert _describe(session, checksum="c" * 32) != ""

    def test_query_failure_does_not_raise(self):
        broken = SimpleNamespace(session=SimpleNamespace(
            query=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))))
        with patch.object(cps, "calibre_db", broken, create=True):
            msg = _kosync()._describe_checksum_miss(CHECKSUM)
        assert "diagnostics unavailable" in msg

    def test_tracking_dict_is_bounded(self, session):
        k = _kosync()
        for i in range(k._MISS_DIAG_MAX_TRACKED + 10):
            _describe(session, checksum=f"{i:032d}")
        assert len(k._miss_diag_last) <= k._MISS_DIAG_MAX_TRACKED


@pytest.mark.unit
def test_lookup_miss_log_line_names_the_stale_book_id():
    """Drive the real lookup: a checksum row whose book is gone must log
    the plain miss line WITH the stale-id cause, and still return no match."""
    from cps.db import Books

    engine = create_engine("sqlite:///:memory:")
    Books.__table__.create(engine)
    BookFormatChecksum.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    s.add(BookFormatChecksum(42, "EPUB", CHECKSUM))
    s.commit()

    k = _kosync()
    with patch.object(cps, "calibre_db", SimpleNamespace(session=s), create=True), \
            patch.object(k.log, "info") as info:
        result = k.get_book_by_checksum(CHECKSUM)
    s.close()

    assert result == (None, None, None, None, None)
    lines = [c.args[0] % c.args[1:] for c in info.call_args_list]
    miss = [line for line in lines if "No book found for checksum: " + CHECKSUM in line]
    assert len(miss) == 1, lines
    assert "book id 42" in miss[0] and "stale" in miss[0], miss[0]


@pytest.mark.unit
def test_many_distinct_misses_stop_querying_and_keep_earlier_ones_quiet(session):
    """A device inventory can report up to 5000 unmatched files in one
    request. Past the per-interval budget the diagnostic must stop querying,
    and a checksum already diagnosed must stay quiet rather than being
    re-diagnosed because the tracking state was reset."""
    k = _kosync()
    calls = {"n": 0}
    real_query = session.query

    def counting_query(*a, **kw):
        calls["n"] += 1
        return real_query(*a, **kw)

    session.query = counting_query
    assert _describe(session, checksum="first".ljust(32, "0")) != ""
    for i in range(k._MISS_DIAG_MAX_TRACKED + 50):
        _describe(session, checksum=f"{i:032d}")
    queries_after_budget = calls["n"]
    for i in range(1000, 1500):
        assert _describe(session, checksum=f"{i:032d}") == ""
    assert calls["n"] == queries_after_budget
    assert _describe(session, checksum="first".ljust(32, "0")) == ""
    assert len(k._miss_diag_last) <= k._MISS_DIAG_MAX_TRACKED


@pytest.mark.unit
def test_row_for_a_book_that_exists_is_not_called_stale():
    """Only a row whose book id is really absent from books is stale. A row
    for a present book (missed because of a version filter) is not."""
    from cps.db import Books

    engine = create_engine("sqlite:///:memory:")
    Books.__table__.create(engine)
    BookFormatChecksum.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    s.execute(Books.__table__.insert().values(id=7, title="Present", sort="Present",
                                              author_sort="", path="p", uuid="u", has_cover=0))
    row = BookFormatChecksum(7, "EPUB", CHECKSUM)
    row.version = "koreader"
    s.add(row)
    s.commit()
    with patch.object(cps, "calibre_db", SimpleNamespace(session=s), create=True):
        msg = _kosync()._describe_checksum_miss(CHECKSUM, version="other")
    s.close()
    assert "stale" not in msg, msg
