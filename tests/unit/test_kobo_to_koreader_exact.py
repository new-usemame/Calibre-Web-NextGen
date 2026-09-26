# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo -> KOReader hand-off lands on the Kobo's sentence, not its percentage (#324).

Driven through the real routes: the Kobo's reading-state PUT
(``HandleStateRequest``) and KOReader's progress GET, over a real SQLite app
database, with ``alice-pg11.epub`` as the library EPUB (the file a KOReader
device holds) and ``alice-pg11.kepub.epub`` as the library KEPUB (the file the
Kobo downloaded). Which XPointer a span must come out as is KOReader's own
engine's word start (``alice-pg11.kobo-spans.json``).

What must hold:
* the span crosses exactly only when it is provably the Kobo's latest report
  behind the row, the Kobo holds the library KEPUB as it is now, and the
  KOReader device holds the library EPUB; otherwise the percentage, as before;
* which position wins is unchanged -- only how a Kobo's winning row is served.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, config, ub
from cps.progress_syncing.checksums.koreader import calculate_koreader_partial_md5
from cps.services import kobo_post_download_restore
from tests.unit.test_koreader_xpointer import FIXTURES

pytestmark = pytest.mark.unit

BOOK_ID = 11
BOOK_UUID = "0b2c6a2e-0000-4000-8000-000000000011"
OTHER_FILE = "0" * 32


def _span(n):
    row = json.loads((FIXTURES / "alice-pg11.kobo-spans.json").read_text(encoding="utf-8"))[n]
    return row["source"], row["span"], row["xpointer"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    kobo = importlib.import_module("cps.kobo")

    engine = create_engine("sqlite:///" + str(tmp_path / "app.db"))
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: session.flush() is None)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: session.commit() is None)
    user = ub.User(name="maggie", email="maggie@example.com", role=0, password="x")
    session.add(user)
    session.commit()
    libra = ub.Device(user_id=user.id, kind="kobo", display_name="Kobo Libra")
    session.add(libra)
    session.commit()

    library = tmp_path / "library"
    book_dir = library / "Lewis Carroll" / "Alice (11)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "Alice - Lewis Carroll.epub"
    kepub = book_dir / "Alice - Lewis Carroll.kepub"
    shutil.copy(FIXTURES / "alice-pg11.epub", epub)
    shutil.copy(FIXTURES / "alice-pg11.kepub.epub", kepub)
    # The KEPUB was converted well before the Kobo downloaded it.
    written = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    os.utime(kepub, (written, written))
    monkeypatch.setattr(config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)
    book = SimpleNamespace(
        id=BOOK_ID, uuid=BOOK_UUID, title="Alice", path="Lewis Carroll/Alice (11)",
        data=[SimpleNamespace(format="EPUB", name="Alice - Lewis Carroll"),
              SimpleNamespace(format="KEPUB", name="Alice - Lewis Carroll")])
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        get_book_by_uuid_for_kobo=lambda *a, **k: book))
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(id=user.id))
    monkeypatch.setattr(kobo, "push_reading_state_to_hardcover", lambda *a, **k: None)

    monkeypatch.setattr(kosync, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(kosync, "authenticate_user", lambda: user)
    monkeypatch.setattr(kosync, "push_reading_state_to_hardcover", lambda *a: None)
    monkeypatch.setattr(kosync, "get_book_checksums", lambda _id: [])
    monkeypatch.setattr(kosync, "enrich_response_with_book_info",
                        lambda response, document: (response, BOOK_ID, "EPUB", book.title, "koreader"))

    app = flask.Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(kosync.kosync)
    client = app.test_client()
    clock = [datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)]

    def kobo_download():
        kobo_post_download_restore.record_download(
            device_id=libra.id, book_id=BOOK_ID, book_format="kepub",
            log=logging.getLogger(__name__))

    def kobo_put(source, span, percent):
        clock[0] += timedelta(minutes=5)
        payload = {"ReadingStates": [{
            "LastModified": clock[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "CurrentBookmark": {
                "ProgressPercent": percent, "ContentSourceProgressPercent": 50,
                "Location": {"Source": source, "Type": "KoboSpan", "Value": span},
            },
            "Statistics": None, "StatusInfo": None,
        }]}
        with app.test_request_context(json=payload, method="PUT"):
            flask.g.annotation_origin_device_id = libra.id
            response = inspect.unwrap(kobo.HandleStateRequest)(BOOK_UUID)
        assert response.get_json()["RequestResult"] == "Success"

    def koreader_push(xpointer, percentage, document):
        response = client.put("/kosync/syncs/progress", json={
            "document": document, "progress": xpointer, "percentage": percentage,
            "device": "KindleBasic5", "device_id": "kindle-1"})
        assert response.status_code == 200, response.get_json()

    def koreader_pull(document):
        session.expire_all()
        return client.get(
            f"/kosync/syncs/progress/{document}?position_kinds=locator,percentage").get_json()

    yield SimpleNamespace(
        session=session, user=user, libra=libra, kepub=kepub, digest=calculate_koreader_partial_md5(str(epub)),
        kobo_download=kobo_download, kobo_put=kobo_put,
        koreader_push=koreader_push, koreader_pull=koreader_pull)
    session.close()
    engine.dispose()


def test_the_kindle_opens_at_the_sentence_the_libra_stopped_on(world):
    source, span, xpointer = _span(200)
    world.kobo_download()
    world.kobo_put(source, span, 37.0)

    body = world.koreader_pull(world.digest)

    assert body["progress"] == xpointer
    assert body["position_kind"] == "locator"
    assert body["percentage"] == pytest.approx(0.37)
    assert body["device"] == "Kobo"


def test_a_kindle_holding_another_copy_gets_the_percentage(world):
    source, span, _xpointer = _span(200)
    world.kobo_download()
    world.kobo_put(source, span, 37.0)

    body = world.koreader_pull(OTHER_FILE)

    assert body["progress"] is None
    assert body["position_kind"] == "percentage"
    assert body["percentage"] == pytest.approx(0.37)


def test_a_libra_with_no_recorded_kepub_download_gets_the_percentage(world):
    # Nothing says which file the Kobo holds, so its span ids prove nothing.
    source, span, _xpointer = _span(200)
    world.kobo_put(source, span, 37.0)

    body = world.koreader_pull(world.digest)

    assert body["progress"] is None
    assert body["position_kind"] == "percentage"


def test_a_kepub_rewritten_since_the_libra_downloaded_it_gives_the_percentage(world):
    source, span, _xpointer = _span(200)
    world.kobo_download()
    world.kobo_put(source, span, 37.0)
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp()
    os.utime(world.kepub, (later, later))  # re-converted after the download

    body = world.koreader_pull(world.digest)

    assert body["progress"] is None
    assert body["position_kind"] == "percentage"


def test_a_span_the_server_moved_since_the_libra_reported_it_gives_the_percentage(world):
    # Re-anchoring a re-converted book moves the stored bookmark to a new span
    # the device has not reported: that span is not what the row was made from.
    source, span, _xpointer = _span(200)
    other_source, other_span, _other = _span(201)
    world.kobo_download()
    world.kobo_put(source, span, 37.0)
    bookmark = world.session.query(ub.KoboBookmark).one()
    bookmark.location_source, bookmark.location_value = other_source, other_span
    world.session.commit()

    body = world.koreader_pull(world.digest)

    assert body["progress"] is None
    assert body["position_kind"] == "percentage"


def test_furthest_still_wins_and_a_kobo_ahead_is_served_exactly(world):
    kindle_place = _span(100)[2]
    source, span, xpointer = _span(300)
    world.kobo_download()
    world.koreader_push(kindle_place, 0.5, world.digest)
    world.kobo_put(*_span(200)[:2], 37.0)  # behind the Kindle: not shared

    body = world.koreader_pull(world.digest)
    assert (body["progress"], body["device"]) == (kindle_place, "KindleBasic5")

    world.kobo_put(source, span, 62.0)  # ahead of it: shared, and exact

    body = world.koreader_pull(world.digest)
    assert (body["progress"], body["position_kind"], body["device"]) == (
        xpointer, "locator", "Kobo")


def test_a_libra_that_paged_back_leaves_the_furthest_place_as_a_percentage(world):
    world.kobo_download()
    world.kobo_put(*_span(300)[:2], 62.0)
    world.kobo_put(*_span(200)[:2], 37.0)  # the same Libra, paged back

    body = world.koreader_pull(world.digest)

    # Furthest still wins: the row stays at 62%. The span stored for it is no
    # longer what the Libra last reported, so nothing proves it any more.
    assert body["percentage"] == pytest.approx(0.62)
    assert body["progress"] is None
    assert body["position_kind"] == "percentage"


def test_a_row_the_web_reader_wrote_is_not_given_the_libras_span(world):
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    world.kobo_download()
    kosync.record_percentage_only_progress(world.user.id, BOOK_ID, 40.0, device="Web reader")
    world.session.commit()
    world.kobo_put(*_span(200)[:2], 40.0)  # the Libra reaches the same percentage

    body = world.koreader_pull(world.digest)

    assert body["device"] == "Web reader"
    assert body["progress"] is None
    assert body["position_kind"] == "percentage"
