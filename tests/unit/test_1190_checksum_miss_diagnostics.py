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

import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import cps
from cps.progress_syncing.models import BookFormatChecksum

CHECKSUM = "9e107d9d372bb6826bd81d3542a419d6"


def _kosync():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def session():
    from cps.db import Books

    engine = create_engine("sqlite:///:memory:")
    Books.__table__.create(engine)
    BookFormatChecksum.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    def reset():
        k = _kosync()
        getattr(k, "_miss_diag_last", {}).clear()
        window = getattr(k, "_miss_diag_window", None)
        if window is not None:
            window.update(start=None, total=None, damage_warned=False)
    reset()
    yield
    reset()


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
        filtered = _kosync()._describe_checksum_miss(CHECKSUM, version="other")
        _kosync()._miss_diag_last.clear()
        # The book can also appear between the lookup and the diagnosis.
        unfiltered = _kosync()._describe_checksum_miss(CHECKSUM)
    s.close()
    assert "stale" not in filtered, filtered
    assert "stale" not in unfiltered, unfiltered


@pytest.fixture
def damaged_index_session():
    """A library whose ``idx_checksum`` no longer agrees with the table (#2960).

    SQLite cannot be told to corrupt an index, so the same state is built
    with a collation whose ordering is flipped after the index is populated:
    the b-tree is then searched in the wrong direction, ``WHERE checksum = ?``
    through the index misses rows, and a scan that bypasses the index still
    finds them. That is what a real damaged index looks like to the server.
    """
    from cps.db import Books

    state = {"flipped": False}

    def collate(a, b):
        r = (a > b) - (a < b)
        return -r if state["flipped"] else r

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_collation("damaged", collate)

    Books.__table__.create(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE book_format_checksums ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER NOT NULL, "
            "format TEXT COLLATE NOCASE NOT NULL, "
            "checksum VARCHAR(32) COLLATE damaged NOT NULL, "
            "version VARCHAR NOT NULL DEFAULT 'koreader', created TIMESTAMP)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX idx_checksum ON book_format_checksums(checksum)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX idx_checksum_version ON book_format_checksums(checksum, version)"
        )
        conn.exec_driver_sql("CREATE INDEX idx_book_format ON book_format_checksums(book, format)")
        conn.exec_driver_sql("CREATE INDEX idx_created ON book_format_checksums(created)")
    s = sessionmaker(bind=engine)()
    checksums = [f"{i:032x}" for i in range(40)]
    for book_id, checksum in enumerate(checksums, start=1):
        s.execute(Books.__table__.insert().values(
            id=book_id, title=f"Book {book_id}", sort=f"Book {book_id}",
            author_sort="", path="p", uuid=f"u{book_id}", has_cover=0))
        s.add(BookFormatChecksum(book_id, "EPUB", checksum))
    s.commit()
    state["flipped"] = True
    # Guard the fixture: the index really must have lost these rows.
    missed = [c for c in checksums if s.execute(
        BookFormatChecksum.__table__.select().where(
            BookFormatChecksum.checksum == c)).first() is None]
    assert missed, "fixture did not put the checksum index out of step"
    yield s, missed[0], checksums.index(missed[0]) + 1
    s.close()


@pytest.mark.unit
def test_damaged_checksum_index_is_named_instead_of_never_registered(damaged_index_session):
    """The row is in the table, so 'registered library-wide but not this one'
    would send the admin hunting for a file the server never served."""
    s, checksum, book_id = damaged_index_session
    msg = _describe(s, checksum=checksum)
    assert "checksum index" in msg and "damaged" in msg, msg
    assert "book id %d" % book_id in msg, msg
    assert "integrity_check" in msg, msg
    assert "never served" not in msg and "stale" not in msg, msg


@pytest.mark.unit
def test_lookup_miss_log_line_names_the_damaged_checksum_index(damaged_index_session):
    """Drive the real lookup: still no match (the index is what failed), but
    the miss line must carry the damaged-index cause."""
    s, checksum, book_id = damaged_index_session
    k = _kosync()
    with patch.object(cps, "calibre_db", SimpleNamespace(session=s), create=True), \
            patch.object(k.log, "info") as info:
        result = k.get_book_by_checksum(checksum)

    assert result == (None, None, None, None, None)
    lines = [c.args[0] % c.args[1:] for c in info.call_args_list]
    miss = [line for line in lines if "No book found for checksum: " + checksum in line]
    assert len(miss) == 1, lines
    assert "damaged" in miss[0] and "book id %d" % book_id in miss[0], miss[0]


@pytest.mark.unit
def test_damaged_index_cause_respects_the_version_filter(damaged_index_session):
    """A row of another version is not the row the lookup wanted, so it must
    not be reported as a damaged index."""
    s, checksum, _book_id = damaged_index_session
    with patch.object(cps, "calibre_db", SimpleNamespace(session=s), create=True):
        msg = _kosync()._describe_checksum_miss(checksum, version="other")
    assert "damaged" not in msg, msg


@pytest.mark.unit
def test_unindexed_probe_failure_does_not_raise(session):
    class Failing:
        def __init__(self, real):
            self._real = real

        def query(self, *a, **k):
            return self._real.query(*a, **k)

        def execute(self, *a, **k):
            raise RuntimeError("boom")

    with patch.object(cps, "calibre_db", SimpleNamespace(session=Failing(session)), create=True):
        msg = _kosync()._describe_checksum_miss(CHECKSUM)
    assert "diagnostics unavailable" in msg


@pytest.mark.unit
def test_damaged_index_logs_one_warning_per_window(damaged_index_session):
    """An admin filtering for warnings must see the damage, but one damaged
    index affects many books, so it is one warning per window, not one per
    checksum; the per-checksum cause stays on the miss line."""
    s, first, first_book = damaged_index_session
    k = _kosync()
    others = [f"{i:032x}" for i in range(40) if f"{i:032x}" != first]
    with patch.object(cps, "calibre_db", SimpleNamespace(session=s), create=True), \
            patch.object(k.log, "warning") as warning:
        k._describe_checksum_miss(first)
        found_second = 0
        for checksum in others:
            if "damaged" in k._describe_checksum_miss(checksum):
                found_second += 1
    assert found_second >= 1, "need a second affected checksum to prove dedupe"
    assert warning.call_count == 1
    line = warning.call_args.args[0] % warning.call_args.args[1:]
    assert "checksum index may be damaged" in line
    assert "book id %d" % first_book in line and first in line, line
    assert "integrity_check" in line, line


@pytest.mark.unit
def test_no_damage_warning_for_an_ordinary_miss(session):
    k = _kosync()
    with patch.object(k.log, "warning") as warning:
        _describe(session)
    warning.assert_not_called()


def test_registration_between_lookup_and_diagnostic_is_not_index_damage(session, monkeypatch, caplog):
    from cps.db import Books

    k = _kosync()
    diagnose = k._describe_checksum_miss

    def register_then_diagnose(*args):
        session.execute(Books.__table__.insert().values(
            id=7, title="New book", sort="New book", author_sort="", path="p", uuid="u", has_cover=0,
        ))
        session.add(BookFormatChecksum(7, "EPUB", CHECKSUM))
        session.commit()
        return diagnose(*args)

    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        with monkeypatch.context() as changes:
            changes.setattr(k, "_describe_checksum_miss", register_then_diagnose)
            assert k.get_book_by_checksum(CHECKSUM)[0] is None
        assert k.get_book_by_checksum(CHECKSUM)[0] == 7

    assert not any("checksum index" in record.message for record in caplog.records)


def test_a_wrong_version_miss_does_not_consume_the_affected_lookup(damaged_index_session, caplog):
    session, checksum, _ = damaged_index_session
    k = _kosync()

    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        assert k.get_book_by_checksum(checksum, "unregistered-version")[0] is None
        assert not any("checksum index" in record.message for record in caplog.records)
        assert k.get_book_by_checksum(checksum, "koreader")[0] is None

    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and "checksum index" in record.message
    ]
    assert len(warnings) == 1


def test_bulk_lookups_skip_index_diagnostics(session, caplog):
    k = _kosync()
    scans = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "NOT INDEXED" in statement:
            scans.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", observe)
    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        for i in range(6):
            assert k.get_book_by_checksum(f"{i:032x}", diagnose_miss=False)[0] is None
    assert not scans
    assert not any("checksum index" in record.message for record in caplog.records)


def test_full_scans_have_their_own_window_budget(session, monkeypatch):
    k = _kosync()
    scans = []
    now = 1_000.0
    monkeypatch.setattr(k, "time", SimpleNamespace(monotonic=lambda: now))

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "NOT INDEXED" in statement:
            scans.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", observe)
    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        for i in range(12):
            k.get_book_by_checksum(f"{i:032x}")
        assert len(scans) == 4

        now += k._MISS_DIAG_INTERVAL - 1.0
        k.get_book_by_checksum(f"{12:032x}")
        assert len(scans) == 4

        now += 1.0
        k.get_book_by_checksum(f"{0:032x}")
        assert len(scans) == 5


def test_unindexed_orphan_is_reported_as_stale(damaged_index_session, caplog):
    from cps.db import Books

    session, checksum, book_id = damaged_index_session
    session.execute(Books.__table__.delete().where(Books.id == book_id))
    session.commit()

    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        assert _kosync().get_book_by_checksum(checksum)[0] is None

    assert any(f"book id {book_id}" in record.message and "stale" in record.message
               for record in caplog.records)
    assert not any("checksum index" in record.message for record in caplog.records)


def test_diagnosis_checks_the_index_used_by_the_failing_lookup(damaged_index_session, caplog):
    session, checksum, book_id = damaged_index_session
    # Rebuild only the composite index so versioned lookups work but the
    # unversioned lookup still misses. This affects the disposable test DB.
    session.execute(text("REINDEX idx_checksum_version"))
    session.commit()
    k = _kosync()

    with patch.object(cps, "calibre_db", SimpleNamespace(session=session), create=True):
        assert k.get_book_by_checksum(checksum, "koreader")[0] == book_id
        assert k.get_book_by_checksum(checksum)[0] is None

    warning = next((record.message for record in caplog.records
                    if record.levelno == logging.WARNING and "checksum index" in record.message), "")
    assert f"book id {book_id}" in warning
