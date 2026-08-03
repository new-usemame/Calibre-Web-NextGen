# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#324 — reading in the web reader must reach the user's other devices.

Before this change the web reader was a read-ONLY consumer of cross-device
progress: ``cps/web.py`` injects ``KoboBookmark.progress_percent`` into the
reader page as a last-resort restore hint, but neither bookmark write route
(``/ajax/bookmark/<id>/<fmt>`` or ``/api/v1/books/<id>/bookmark``) ever wrote
back.  ``ub.Bookmark`` — the CFI store both readers share — is read by nothing
except the readers themselves, so a browser reading session was invisible to
the Kobo device and to the book-detail progress row.

The bridge is the percentage: the client already computes it (epub.js
``locations.percentageFromCfi``), and ``KoboBookmark.progress_percent`` is
exactly what the Kobo sync feed serves back to the device.  The parent
``KoboReadingState.last_modified`` bump that makes the feed pick the row up is
handled by the ``before_flush`` listener in ``cps/ub.py``.

Pinned here:
  * a forward position advances the shared carrier and creates the state graph;
  * a *backward* position never regresses another device's furthest progress;
  * 0% / absent / malformed percentages write nothing (the CWA #1364 "fake 0"
    class, where a percentage read before ``locations.generate()`` resolves is
    a meaningless 0 that would otherwise wipe a real position);
  * both bookmark routes thread the field through;
  * both clients actually send it (a server that accepts a field no client
    sends is a no-op feature).
"""

import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub

REPO = Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────

def _session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_progress(session, user_id, book_id, percent):
    """Create the same ReadBook -> KoboReadingState -> KoboBookmark graph the
    Kobo device and the KOReader sync path both write into."""
    read = ub.ReadBook(user_id=user_id, book_id=book_id,
                       read_status=ub.ReadBook.STATUS_IN_PROGRESS)
    state = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    state.current_bookmark = ub.KoboBookmark(progress_percent=percent)
    read.kobo_reading_state = state
    session.add(read)
    session.commit()
    return read


def _stored_percent(session, user_id, book_id):
    state = (session.query(ub.KoboReadingState)
             .filter(ub.KoboReadingState.user_id == user_id,
                     ub.KoboReadingState.book_id == book_id).first())
    if state is None or state.current_bookmark is None:
        return None
    return state.current_bookmark.progress_percent


def _service(monkeypatch, session):
    from cps.services import reading_position as mod
    monkeypatch.setattr(ub, "session", session)
    # update_book_read_status consults the custom-read-column setting, which a
    # bare unit-test ConfigSQL doesn't carry (same shim as test_627_*).
    # cps.progress_syncing.protocols re-exports Blueprints that shadow the
    # submodule attribute, so reach the module through sys.modules.
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    monkeypatch.setattr(kosync.config, "config_read_column", 0, raising=False)
    return mod


# ── percentage coercion (defends the "fake 0" / junk-input class) ────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("42.5", 42.5), (42.5, 42.5), (0, 0.0), (100, 100.0), ("100", 100.0),
])
def test_coerce_percentage_accepts_valid(raw, expected):
    from cps.services.reading_position import coerce_percentage
    assert coerce_percentage(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw", [
    None, "", "abc", "NaN", "inf", "-inf", float("nan"), float("inf"),
    -1, 101, "101", True, False, {}, [],
])
def test_coerce_percentage_rejects_invalid(raw):
    """Out-of-range, non-numeric and bool inputs must not reach the DB."""
    from cps.services.reading_position import coerce_percentage
    assert coerce_percentage(raw) is None


# ── the write-through itself ─────────────────────────────────────────────────

@pytest.mark.unit
def test_forward_progress_advances_shared_carrier(monkeypatch):
    """THE headline behaviour: reading in the browser reaches the Kobo carrier."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=20.0)

    advanced = mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 55.0)
    session.commit()

    assert advanced is True
    assert _stored_percent(session, 7, 42) == 55.0


@pytest.mark.unit
def test_backward_progress_never_regresses_another_device(monkeypatch):
    """Opening the book in the browser at chapter 1 must not wipe the Kobo's 80%."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=80.0)

    advanced = mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 5.0)
    session.commit()

    assert advanced is False
    assert _stored_percent(session, 7, 42) == 80.0


@pytest.mark.unit
@pytest.mark.parametrize("incoming", [50.0, 20.0])
def test_rejected_write_does_not_touch_parent_last_modified(monkeypatch, incoming):
    """A rejected write must be inert. KoboReadingState.last_modified is what
    gates the Kobo sync feed (cps/kobo.py:544, :691), so bumping it on an equal
    or backward position would push the device a payload carrying no news."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=50.0)
    before = (session.query(ub.KoboReadingState)
              .filter(ub.KoboReadingState.book_id == 42).first().last_modified)

    assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, incoming) is False
    session.commit()

    state = (session.query(ub.KoboReadingState)
             .filter(ub.KoboReadingState.book_id == 42).first())
    assert state.last_modified == before, "rejected write must not churn the Kobo feed"
    assert _stored_percent(session, 7, 42) == 50.0


@pytest.mark.unit
def test_first_ever_position_creates_the_state_graph(monkeypatch):
    """A book with no prior progress at all must get the full carrier graph, so
    the book-detail row and the Kobo feed both see it (the #627 shape)."""
    session = _session()
    mod = _service(monkeypatch, session)

    advanced = mod.record_web_reader_progress(SimpleNamespace(id=7), 99, 33.0)
    session.commit()

    assert advanced is True
    assert _stored_percent(session, 7, 99) == 33.0
    read = (session.query(ub.ReadBook)
            .filter(ub.ReadBook.user_id == 7, ub.ReadBook.book_id == 99).first())
    assert read is not None
    assert read.read_status == ub.ReadBook.STATUS_IN_PROGRESS


@pytest.mark.unit
def test_zero_percent_writes_nothing(monkeypatch):
    """CWA #1364 class: a percentage sampled before locations.generate() resolves
    is a meaningless 0. It must never create or overwrite a position."""
    session = _session()
    mod = _service(monkeypatch, session)

    assert mod.record_web_reader_progress(SimpleNamespace(id=7), 99, 0.0) is False
    session.commit()
    assert _stored_percent(session, 7, 99) is None
    assert session.query(ub.ReadBook).count() == 0


@pytest.mark.unit
def test_completion_marks_the_book_finished(monkeypatch):
    """Finishing a book in the browser should mark it read, like KOReader does."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=90.0)

    assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 100.0) is True
    session.commit()

    read = (session.query(ub.ReadBook)
            .filter(ub.ReadBook.user_id == 7, ub.ReadBook.book_id == 42).first())
    assert read.read_status == ub.ReadBook.STATUS_FINISHED


@pytest.mark.unit
def test_writeback_bumps_parent_last_modified_for_the_kobo_feed(monkeypatch):
    """The Kobo sync feed only emits a ReadingState when the PARENT row's
    last_modified advances (cps/kobo.py:544, :691). Writing only the child
    KoboBookmark would leave the device unaware — i.e. a no-op feature."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=10.0)
    before = (session.query(ub.KoboReadingState)
              .filter(ub.KoboReadingState.book_id == 42).first().last_modified)

    mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 70.0)
    session.commit()

    after = (session.query(ub.KoboReadingState)
             .filter(ub.KoboReadingState.book_id == 42).first().last_modified)
    assert after > before, "parent last_modified must advance or Kobo never syncs it"


@pytest.mark.unit
def test_progress_failure_does_not_cost_the_user_their_bookmark(monkeypatch):
    """The savepoint is the whole promise of the try/except in the routes: an
    IntegrityError from the concurrent-first-write race surfaces at FLUSH time,
    which ub.session_commit does not catch. The progress write must roll back
    alone, leaving the caller's bookmark committable."""
    session = _session()
    mod = _service(monkeypatch, session)

    # The caller's pending bookmark write, exactly as the routes leave it.
    session.merge(ub.Bookmark(user_id=7, book_id=42, format="epub",
                              bookmark_key="epubcfi(/6/8!/4/2)"))

    boom = sys.modules["cps.progress_syncing.protocols.kosync"]
    monkeypatch.setattr(boom, "update_book_read_status",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("flush blew up")))

    assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 55.0) is False
    session.commit()  # must not raise

    row = (session.query(ub.Bookmark)
           .filter(ub.Bookmark.user_id == 7, ub.Bookmark.book_id == 42).first())
    assert row is not None, "a failed progress share must not lose the bookmark"
    assert row.bookmark_key == "epubcfi(/6/8!/4/2)"


# ── the finished threshold must not be reachable by display rounding ─────────

@pytest.mark.unit
def test_clients_sync_the_unrounded_percentage():
    """The server finishes a book at >= 99%. Both readers round for display, so
    syncing the rounded figure would mark a book read for someone at an actual
    98.5%. The synced value must be the unrounded one."""
    js = (REPO / "cps/static/js/reading/epub-progress.js").read_text(encoding="utf-8")
    assert "calculateProgressExact" in js, \
        "classic reader must sync an unrounded percentage"
    assert re.search(r"scheduleCfiSave\(\s*cfi\s*,\s*calculateProgressExact\(\)\s*\)", js), \
        "the save call must use the exact value, not the rounded display figure"

    tsx = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    assert re.search(r"persistCfi\(\s*cfi\s*,\s*exact\s*\)", tsx), \
        "SPA reader must persist the unrounded percentage"
    assert re.search(r"setProgress\(\s*Math\.round\(\s*exact\s*\)\s*\)", tsx), \
        "rounding must remain a display concern"


@pytest.mark.unit
def test_a_position_below_the_threshold_does_not_finish_the_book(monkeypatch):
    """98.5% is 'nearly done', not 'done'. Pinning the boundary the rounding
    fix protects."""
    session = _session()
    mod = _service(monkeypatch, session)
    _seed_progress(session, user_id=7, book_id=42, percent=10.0)

    assert mod.record_web_reader_progress(SimpleNamespace(id=7), 42, 98.5) is True
    session.commit()

    read = (session.query(ub.ReadBook)
            .filter(ub.ReadBook.user_id == 7, ub.ReadBook.book_id == 42).first())
    assert read.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert _stored_percent(session, 7, 42) == 98.5


# ── route wiring: both bookmark writers thread the field ─────────────────────

@pytest.mark.unit
def test_api_v1_save_bookmark_records_progress():
    """SPA route passes a supplied percentage to the write-through."""
    from cps.api import reader as mod
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.test_request_context(
        "/api/v1/books/5/bookmark", method="POST",
        json={"format": "epub", "bookmark": "epubcfi(/6/8)", "percentage": 61},
        content_type="application/json")
    recorder = MagicMock()
    with ctx:
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(mod, "ub", MagicMock()), \
             patch("cps.services.reading_position.record_web_reader_progress", recorder):
            inspect.unwrap(mod.save_bookmark)(5)
    assert recorder.called, "SPA bookmark save must write progress through"
    assert recorder.call_args[0][1] == 5
    assert recorder.call_args[0][2] == 61.0


@pytest.mark.unit
def test_api_v1_clearing_bookmark_records_nothing():
    """An empty bookmark is a CLEAR — it must not push a position."""
    from cps.api import reader as mod
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.test_request_context(
        "/api/v1/books/5/bookmark", method="POST",
        json={"format": "epub", "bookmark": "", "percentage": 61},
        content_type="application/json")
    recorder = MagicMock()
    with ctx:
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(mod, "ub", MagicMock()), \
             patch("cps.services.reading_position.record_web_reader_progress", recorder):
            inspect.unwrap(mod.save_bookmark)(5)
    assert not recorder.called


@pytest.mark.unit
def test_api_v1_save_without_percentage_records_nothing():
    """The comic/audio readers reuse these routes and send no percentage."""
    from cps.api import reader as mod
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.test_request_context(
        "/api/v1/books/5/bookmark", method="POST",
        json={"format": "epub", "bookmark": "epubcfi(/6/8)"},
        content_type="application/json")
    recorder = MagicMock()
    with ctx:
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(mod, "ub", MagicMock()), \
             patch("cps.services.reading_position.record_web_reader_progress", recorder):
            inspect.unwrap(mod.save_bookmark)(5)
    assert not recorder.called


@pytest.mark.unit
def test_classic_set_bookmark_records_progress():
    """The classic epub.js reader posts form-encoded to /ajax/bookmark."""
    from cps import web as mod
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.test_request_context(
        "/ajax/bookmark/5/epub", method="POST",
        data={"bookmark": "epubcfi(/6/8)", "percentage": "61"})
    recorder = MagicMock()
    with ctx:
        with patch.object(mod, "current_user", SimpleNamespace(id=1)), \
             patch.object(mod, "ub", MagicMock()), \
             patch("cps.services.reading_position.record_web_reader_progress", recorder):
            inspect.unwrap(mod.set_bookmark)(5, "epub")
    assert recorder.called, "classic bookmark save must write progress through"
    assert recorder.call_args[0][2] == 61.0


@pytest.mark.unit
def test_classic_clearing_bookmark_records_nothing():
    from cps import web as mod
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.test_request_context(
        "/ajax/bookmark/5/epub", method="POST",
        data={"bookmark": "", "percentage": "61"})
    recorder = MagicMock()
    with ctx:
        with patch.object(mod, "current_user", SimpleNamespace(id=1)), \
             patch.object(mod, "ub", MagicMock()), \
             patch("cps.services.reading_position.record_web_reader_progress", recorder):
            inspect.unwrap(mod.set_bookmark)(5, "epub")
    assert not recorder.called


# ── the clients must actually send it (anti-no-op pins) ──────────────────────

@pytest.mark.unit
def test_classic_reader_js_sends_percentage():
    src = (REPO / "cps/static/js/reading/epub-progress.js").read_text(encoding="utf-8")
    assert "percentage=" in src, "epub-progress.js must post the percentage"
    # the value has to come from the guarded calculation, not a bare 0
    assert re.search(r"scheduleCfiSave\(\s*cfi\s*,", src), \
        "scheduleCfiSave must carry the percentage alongside the CFI"


@pytest.mark.unit
def test_spa_reader_sends_percentage():
    src = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    assert "percentage" in src, "Reader.tsx must send the percentage with the bookmark"
    queries = (REPO / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")
    assert "percentage" in queries, "useSaveBookmark must accept a percentage"


@pytest.mark.unit
def test_service_module_has_spdx_header():
    src = (REPO / "cps/services/reading_position.py").read_text(encoding="utf-8")
    assert "SPDX-License-Identifier: GPL-3.0-or-later" in src


# ── the synced percentage must belong to the CFI it is sent with ─────────────
#
# Cross-family review (Terra, 2026-08-03) found the SPA carried the percentage
# forward as sticky state. Two reachable ways that corrupts a position:
#
#   1. Different book. `<Reader id={p.id} />` had no `key`, so wouter reuses the
#      component instance across an id change and every ref survives. Book B's
#      first `relocated` fires before `locations.generate()` resolves, so the
#      new percentage is undefined, the ref still holds Book A's — and the save
#      posts Book A's percentage under Book B's id. At 100% that marks a book
#      the user never opened as FINISHED, and the Kobo feed distributes it.
#   2. Same book. A genuine 0% left the previous positive value in the ref, so
#      the client re-sent a position the reader is not at — which resurrects a
#      cleared position if the carrier was reset while the reader stayed open.
#
# Both are client-side, and the frontend has no vitest harness yet (SPA
# behavioural coverage is the tracked Layer-2 gap), so these pin the structure
# that makes the bug impossible rather than the rendered behaviour.

@pytest.mark.unit
def test_spa_percentage_is_not_sticky_across_relocations():
    """A relocation that yields no usable percentage must CLEAR the ref, never
    leave the previous value to be posted against the new CFI."""
    tsx = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")

    assert not re.search(r"if\s*\([^)]*\)\s*lastPercentRef\.current\s*=\s*percentage", tsx), \
        "a guarded assignment leaves the previous percentage behind on a 0/undefined sample"
    assert re.search(r"lastPercentRef\.current\s*=\s*valid\s*;", tsx), \
        "the ref must be assigned unconditionally so an unusable sample clears it"
    # Scoped to persistCfi: the unmount flush legitimately reads both refs, and
    # they now always correspond because persistCfi writes them together.
    body = tsx[tsx.index("const persistCfi"):tsx.index("}, 800);")]
    assert "lastPercentRef.current" not in body.split("saveTimer.current = setTimeout")[1], \
        "the debounced save must use the value computed for THIS cfi, not re-read the ref"
    assert re.search(r"percentage:\s*valid", body), \
        "the posted percentage must be the one validated alongside this cfi"


@pytest.mark.unit
def test_spa_reader_is_keyed_by_book_id():
    """A different book is a different reading session. Without a key, React
    reuses the Reader instance across an id change and its refs (last CFI, last
    percentage, pending save timer) leak into the next book."""
    app = (REPO / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert re.search(r"<Reader\s+key=\{p\.id\}\s+id=\{p\.id\}\s*/>", app), \
        "Reader must be keyed by book id so a book change remounts it"


@pytest.mark.unit
def test_progress_lookup_does_not_autoflush_the_callers_bookmark():
    """The caller has a pending bookmark write when this runs. A bare query
    would autoflush it, making a failure of the user's REQUIRED write surface
    inside this best-effort helper — where both routes log and swallow it as an
    optional progress failure."""
    src = (REPO / "cps/services/reading_position.py").read_text(encoding="utf-8")
    assert "with ub.session.no_autoflush:" in src, \
        "the KoboReadingState lookup must not autoflush the caller's pending write"
    lookup = src.index("query(ub.KoboReadingState)")
    guard = src.index("with ub.session.no_autoflush:")
    settle = src.index("ub.session.flush()")
    assert guard < lookup < settle, \
        "the no_autoflush guard must wrap the lookup, which must precede the settling flush"
