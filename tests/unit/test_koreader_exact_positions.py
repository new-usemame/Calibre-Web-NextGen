# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader <-> web reader hand-offs land on the same place, not the same percentage (#324).

Driven through the real routes -- KOReader's progress PUT/GET, the web reader's
bookmark save and resume read -- over a real SQLite app database and the
Metamorphosis EPUB as a library file. The positions are the ones a Kindle
recorded (``tests/fixtures/koreader_xpointer/metamorphosis-221.pages.json``),
and a CFI is judged by the text epub.js would find there (``_EpubJs``).

What must hold:
* a position crosses exactly only when it is provably about the same file on
  both sides (KOReader's partial MD5 of the bytes), otherwise as a percentage;
* nothing about which position wins changes -- only how the winner is served.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import shutil
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, config, ub
from cps.progress_syncing.checksums.koreader import calculate_koreader_partial_md5
from cps.progress_syncing.models import KOSyncProgress
from cps.services import kobo_resume, reading_position, reading_sources
from tests.unit.test_koreader_xpointer import FIXTURES, _EpubJs, _squash

pytestmark = pytest.mark.unit

BOOK_ID = 221
OTHER_FILE = "0" * 32  # a device holding some other copy of the book


def _page(n):
    """(xpointer, text shown at the top of that page) from the Kindle."""
    sample = json.loads((FIXTURES / "metamorphosis-221.pages.json").read_text())["samples"][n]
    return sample["xpointer"], sample["text"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    from cps.api import reader

    engine = create_engine("sqlite:///" + str(tmp_path / "app.db"))
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda: session.flush() is None)
    monkeypatch.setattr(ub, "session_commit", lambda *a: session.commit() is None)
    user = ub.User(name="reader", email="reader@example.com", role=0, password="x")
    session.add(user)
    session.commit()

    library = tmp_path / "library"
    book_dir = library / "Franz Kafka" / "Metamorphosis (221)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "Metamorphosis - Franz Kafka.epub"
    shutil.copy(FIXTURES / "metamorphosis-221.epub", epub)
    metadata = sqlite3.connect(tmp_path / "metadata.db")
    metadata.executescript(
        "CREATE TABLE books (id INTEGER, path TEXT);"
        "CREATE TABLE data (book INTEGER, format TEXT, name TEXT);"
        f"INSERT INTO books VALUES ({BOOK_ID}, 'Franz Kafka/Metamorphosis (221)');"
        f"INSERT INTO data VALUES ({BOOK_ID}, 'EPUB', 'Metamorphosis - Franz Kafka');")
    metadata.close()
    monkeypatch.setattr(config, "config_calibre_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)
    book = SimpleNamespace(
        id=BOOK_ID, title="Metamorphosis", path="Franz Kafka/Metamorphosis (221)",
        data=[SimpleNamespace(format="EPUB", name="Metamorphosis - Franz Kafka")])
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)

    monkeypatch.setattr(kosync, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(kosync, "authenticate_user", lambda: user)
    monkeypatch.setattr(kosync, "push_reading_state_to_hardcover", lambda *a: None)
    monkeypatch.setattr(kosync, "get_book_checksums", lambda _id: [])
    # Every digest in these tests names book 221 (the book-level lookup is
    # not what is under test); WHICH file a digest names is.
    monkeypatch.setattr(kosync, "enrich_response_with_book_info",
                        lambda response, document: (response, BOOK_ID, "EPUB", book.title, "koreader"))
    # Conversions run on a worker with a request budget; give the test's cold
    # parse room so the result does not depend on the machine's speed.
    monkeypatch.setattr(kobo_resume, "RESUME_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(kobo_resume, "_CACHE", OrderedDict())

    web_user = SimpleNamespace(id=user.id, is_authenticated=True, is_anonymous=False)
    monkeypatch.setattr(reader, "current_user", web_user)
    app = flask.Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(kosync.kosync)
    client = app.test_client()

    def koreader_push(xpointer, percentage, document, device_id="kindle-1"):
        response = client.put("/kosync/syncs/progress", json={
            "document": document, "progress": xpointer, "percentage": percentage,
            "device": "KindleBasic5", "device_id": device_id})
        assert response.status_code == 200, response.get_json()
        # The push's conversion for the web reader runs off the request.
        _drain_resume_workers()

    def koreader_pull(document, advertise=True):
        url = f"/kosync/syncs/progress/{document}"
        if advertise:
            url += "?position_kinds=locator,percentage"
        return client.get(url).get_json()

    def web_save(cfi, percentage):
        with app.test_request_context("/", method="POST", json={
                "format": "epub", "bookmark": cfi, "percentage": percentage}):
            result = inspect.unwrap(reader.save_bookmark)(BOOK_ID)
        assert result[1] == 204

    def web_resume():
        session.expire_all()
        with app.test_request_context("/"):
            return inspect.unwrap(reader.get_bookmark)(BOOK_ID).get_json()["resume"]

    yield SimpleNamespace(
        session=session, user=user, epub=epub, digest=calculate_koreader_partial_md5(str(epub)),
        koreader_push=koreader_push, koreader_pull=koreader_pull,
        web_save=web_save, web_resume=web_resume)
    session.close()
    engine.dispose()


def _drain_resume_workers():
    """Wait for background conversions (bounded by the module's two slots)."""
    for _ in range(2):
        assert kobo_resume._SLOTS.acquire(timeout=5)
    for _ in range(2):
        kobo_resume._SLOTS.release()


def _cfi_page_text(epub, cfi):
    return _squash(_EpubJs(epub).point_text(cfi))


# ---------------------------------------------------------------------------
# KOReader -> web reader
# ---------------------------------------------------------------------------


def test_web_reader_opens_at_the_kindle_page_not_its_percentage(world):
    xpointer, shown = _page(60)
    world.koreader_push(xpointer, 0.5143, world.digest)

    resume = world.web_resume()

    assert resume["percentage"] == pytest.approx(51.43)
    assert resume["cfi"], resume
    assert _cfi_page_text(world.epub, resume["cfi"]).startswith(_squash(shown)[:80])
    # The browser checks these are the bytes it renders before trusting the CFI.
    assert resume["epub_sha256"] == hashlib.sha256(world.epub.read_bytes()).hexdigest()


def test_a_kindle_position_from_another_file_stays_a_percentage(world):
    xpointer, _shown = _page(60)
    world.koreader_push(xpointer, 0.5143, OTHER_FILE)

    resume = world.web_resume()

    assert resume["percentage"] == pytest.approx(51.43)
    assert "cfi" not in resume


def test_a_newer_position_from_elsewhere_is_not_replaced_by_the_kindle_page(world):
    xpointer, _shown = _page(60)
    world.koreader_push(xpointer, 0.5143, world.digest)
    # Another browser reads further; that position, not the Kindle's, is
    # the one to resume, and only as a percentage (its CFI is its own).
    world.web_save("epubcfi(/6/10!/4/2/4/1:0)", 70.0)
    world.session.query(ub.Bookmark).delete()
    world.session.commit()

    resume = world.web_resume()

    assert resume["percentage"] == pytest.approx(70.0)
    assert "cfi" not in resume


def test_a_kindle_that_turned_back_does_not_pull_the_web_reader_back(world):
    world.koreader_push(_page(60)[0], 0.5143, world.digest)
    # The same Kindle turns back: its own row follows it, the shared bookmark
    # keeps the furthest place -- which is not the Kindle's current page.
    world.koreader_push(_page(30)[0], 0.3, world.digest)

    resume = world.web_resume()

    assert resume["percentage"] == pytest.approx(51.43)
    assert "cfi" not in resume


def test_a_later_kobo_report_at_the_same_percentage_keeps_its_own_place(world, monkeypatch):
    world.koreader_push(_page(60)[0], 0.5143, world.digest)
    # A Kobo later reports the very same percentage with its own span, which
    # the bookmark takes (Kobo PUTs accept an equal, newer report).
    world.session.execute(text(
        "UPDATE kobo_bookmark SET location_source='chapter.xhtml', location_type='KoboSpan', "
        "location_value='kobo.1.2', last_modified=:later"),
        {"later": datetime.now(timezone.utc) + timedelta(minutes=5)})
    world.session.commit()
    real = kobo_resume._resolve

    def resolve(book_id, source, kind, value):
        if kind == "KoboSpan":
            return {"cfi": "epubcfi(/6/4!/4/2/1:0)", "epub_sha256": "kobo"}
        return real(book_id, source, kind, value)
    monkeypatch.setattr(kobo_resume, "_resolve", resolve)

    assert world.web_resume()["cfi"] == "epubcfi(/6/4!/4/2/1:0)"


def test_the_kindle_journal_row_places_it_exactly_in_the_reading_sources(world):
    xpointer, shown = _page(40)
    world.koreader_push(xpointer, 0.35, world.digest)
    world.session.expire_all()
    devices = world.session.query(ub.Device).filter_by(kind="koreader").all()
    positions = world.session.query(ub.DeviceReadingPosition).all()

    rows = reading_sources.device_source_rows(devices, positions, book_id=BOOK_ID,
                                              epub_path=world.epub)

    (kindle,) = rows
    assert kindle["kind"] == "koreader" and kindle["resume"]["exact"] is True
    assert _cfi_page_text(world.epub, kindle["resume"]["cfi"]).startswith(_squash(shown)[:80])


def test_a_stale_kobo_span_on_the_bookmark_is_not_used_for_a_kindle_position(
        world, monkeypatch):
    # A Kobo synced this book at 20%: its span is on the shared bookmark.
    state = ub.KoboReadingState(user_id=world.user.id, book_id=BOOK_ID)
    state.current_bookmark = ub.KoboBookmark(
        progress_percent=20.0, last_modified=datetime.now(timezone.utc),
        location_source="chapter.xhtml", location_type="KoboSpan", location_value="kobo.1.2")
    read_book = ub.ReadBook(user_id=world.user.id, book_id=BOOK_ID,
                            read_status=ub.ReadBook.STATUS_IN_PROGRESS)
    read_book.kobo_reading_state = state
    world.session.add(read_book)
    world.session.commit()
    real = kobo_resume._resolve

    def resolve(book_id, source, kind, value):
        if kind == "KoboSpan":
            return {"cfi": "epubcfi(/6/4!/4/2/1:0)", "epub_sha256": "kobo"}
        return real(book_id, source, kind, value)
    monkeypatch.setattr(kobo_resume, "_resolve", resolve)
    assert world.web_resume()["cfi"] == "epubcfi(/6/4!/4/2/1:0)"  # the Kobo's own place

    # The Kindle then reads further, from a file the web reader does not render.
    world.koreader_push(_page(60)[0], 0.5143, OTHER_FILE)
    resume = world.web_resume()

    assert resume["percentage"] == pytest.approx(51.43)
    assert "cfi" not in resume, "the span describes 20%, not where the Kindle is"


# ---------------------------------------------------------------------------
# web reader -> KOReader
# ---------------------------------------------------------------------------


def _web_cfi(n):
    """The CFI epub.js has at the top of Kindle page ``n`` (its text is checked)."""
    from cps.services.koreader_xpointer import xpointer_to_cfi
    xpointer, _shown = _page(n)
    return xpointer, xpointer_to_cfi(FIXTURES / "metamorphosis-221.epub", xpointer)


def test_kindle_opens_at_the_web_reader_place_in_the_file_it_holds(world):
    xpointer, cfi = _web_cfi(25)
    world.web_save(cfi, 21.7)

    body = world.koreader_pull(world.digest)

    assert body["position_kind"] == "locator"
    assert body["progress"] == xpointer
    assert body["percentage"] == pytest.approx(0.217)
    assert body["device"] == "Web reader"
    # Derived for this device's file on each request; the shared row keeps
    # the sentinel, so another device's file never inherits the XPointer.
    stored = world.session.query(KOSyncProgress).one()
    assert stored.progress == "cwng:percentage"


def test_a_device_holding_another_file_gets_the_percentage(world):
    _xpointer, cfi = _web_cfi(25)
    world.web_save(cfi, 21.7)

    body = world.koreader_pull(OTHER_FILE)

    assert body["position_kind"] == "percentage" and body["progress"] is None
    assert body["percentage"] == pytest.approx(0.217)


def test_only_the_cfi_that_made_the_shared_position_is_converted(world):
    _xpointer, cfi = _web_cfi(25)
    world.web_save(cfi, 21.7)
    # The same browser then turns back a few pages: saved, but not shared
    # (furthest wins), so its bookmark no longer describes the shared 21.7%.
    world.web_save(_web_cfi(24)[1], 20.9)

    body = world.koreader_pull(world.digest)

    assert body["position_kind"] == "percentage" and body["progress"] is None
    assert body["percentage"] == pytest.approx(0.217)


def test_an_older_plugin_still_never_receives_a_web_reader_row(world):
    _xpointer, cfi = _web_cfi(25)
    world.web_save(cfi, 21.7)

    body = world.koreader_pull(world.digest, advertise=False)

    assert "progress" not in body and "percentage" not in body


def test_furthest_position_still_wins_both_ways(world):
    xpointer, cfi = _web_cfi(25)
    world.web_save(cfi, 21.7)
    # A Kindle behind the browser does not move the shared position back...
    world.koreader_push(_page(10)[0], 0.08, world.digest)
    body = world.koreader_pull(world.digest)
    assert (body["progress"], body["percentage"]) == (xpointer, pytest.approx(0.217))
    # ...and once it reads past it, its own place is what both readers get.
    ahead, shown = _page(60)
    world.koreader_push(ahead, 0.5143, world.digest)
    body = world.koreader_pull(world.digest)
    assert (body["progress"], body["position_kind"]) == (ahead, "locator")
    resume = world.web_resume()
    assert _cfi_page_text(world.epub, resume["cfi"]).startswith(_squash(shown)[:80])
