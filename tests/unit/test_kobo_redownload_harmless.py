# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo (re-)download must be harmless.

OBSERVED 2026-09-11 on a household Libra Colour (book 567): a re-converted
EPUB was legitimately re-sent; the reader's re-download minted the KEPUB on
demand, which advanced ``Books.last_modified`` and re-sent the entitlement a
second time 25 s after she had re-found her place. After each download the
device asked ``/annotations`` and CWNG proxied an empty Kobo cloud set over
her 22 locally held highlights.

Three layers, each with its own seen-red test here:

* A1 — a derived sibling format (EPUB -> KEPUB) does not advance the clock and
  does not change the entitlement fingerprint.
* B1 — the first annotation GET after a device download is answered from
  CWNG's own rows.
* B2 — rows whose chapter vanished from the current KEPUB are re-anchored by
  their highlighted text before they are served.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g, make_response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.readingservices as rs
from cps.services import kobo_annotation_authority as authority
from cps.services import kobo_annotation_reanchor as reanchor
from cps.services import kobo_post_download_restore as ledger

from tests.unit.test_1925_kobo_sync_dedownload import (  # noqa: F401 - fixture
    _entitlements,
    sync_harness,
)

pytestmark = pytest.mark.unit

OWNED = "c008eeb9-9ab2-4d0e-9af4-77b0abf73c97"
BOOK_ID = 567
USER_ID = 3
DEVICE_ID = 1


# ---------------------------------------------------------------- A1: clock


def _book(*formats):
    return SimpleNamespace(
        id=1, last_modified=datetime(2026, 9, 1, tzinfo=timezone.utc),
        data=[SimpleNamespace(format=f, name="b") for f in formats],
    )


def test_first_kobo_deliverable_format_advances_the_clock(monkeypatch):
    from cps import helper

    monkeypatch.setattr(helper.calibre_db, "set_metadata_dirty", lambda *_a: None, raising=False)
    book = _book("PDF")
    before = book.last_modified

    assert helper.mark_book_format_materialised(book, "EPUB") is True
    assert book.last_modified > before


def test_derived_sibling_format_leaves_the_clock_alone(monkeypatch):
    from cps import helper

    book = _book("EPUB")
    before = book.last_modified

    assert helper.mark_book_format_materialised(book, "KEPUB") is False
    assert book.last_modified == before
    assert helper.mark_book_format_materialised(_book("KEPUB", "PDF"), "EPUB") is False


def test_on_demand_kepub_conversion_does_not_touch_last_modified(tmp_path, monkeypatch):
    """The convert task path that ran on the household instance."""
    import cps.helper  # noqa: F401 - normal import order
    from cps.tasks import convert

    book_path = tmp_path / "hellenistic"
    (tmp_path / "hellenistic.epub").write_bytes(b"source")
    (tmp_path / "hellenistic.kepub").write_bytes(b"derived")
    book = SimpleNamespace(
        id=BOOK_ID, title="Hellenistic Astrology", path="Brennan/Hellenistic",
        last_modified=datetime(2026, 9, 10, 23, 6, tzinfo=timezone.utc),
        data=[SimpleNamespace(name="hellenistic", format="EPUB")],
    )
    clock_before = book.last_modified

    class Query:
        def filter(self, *_a):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, *_a):
            return Query()

        def merge(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class LocalDB:
        def __init__(self, **_k):
            self.session = Session()

        def get_book(self, _id):
            return book

        def get_book_format(self, *_a):
            return None

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert, "_log_fragment_anchored_toc", lambda *_a: None)
    task = convert.TaskConvert(
        str(book_path), BOOK_ID, "convert",
        {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None,
    )
    monkeypatch.setattr(task, "_convert_kepubify", lambda *_a: (0, None))
    monkeypatch.setattr(task, "_handleSuccess", lambda: None)

    assert task._convert_ebook_format() == "hellenistic.kepub"
    assert book.last_modified == clock_before


def _materialise_kepub(sync_harness, size=2_345_678):
    """What the on-demand convert task does: add the KEPUB row, notify the SSOT."""
    from cps import db, helper

    sync_harness.session.add(db.Data(
        sync_harness.book.id, "KEPUB", size, "stable-book",
    ))
    sync_harness.session.commit()
    sync_harness.session.expire(sync_harness.book, ["data"])
    helper.mark_book_format_materialised(sync_harness.book, "KEPUB")
    sync_harness.session.commit()


def test_materialised_kepub_is_not_resent_on_the_next_sync(sync_harness, monkeypatch):
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1
    token = first.headers[sync_harness.token_header]

    _materialise_kepub(sync_harness)
    second = sync_harness.sync(token)

    assert _entitlements(second) == [], (
        "a KEPUB minted from the stored EPUB must not re-send the entitlement; "
        "Nickel answers a ChangedEntitlement by de-downloading the book"
    )


def test_materialised_kepub_size_change_is_suppressed_on_a_stale_token(
    sync_harness, monkeypatch, caplog,
):
    """The Size of the served row changes; the source bytes did not."""
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    caplog.set_level(logging.DEBUG, logger="cps.kobo")
    first = sync_harness.sync()
    assert len(_entitlements(first)) == 1

    _materialise_kepub(sync_harness)
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()
    second = sync_harness.sync(stale_token)

    assert _entitlements(second) == []
    summaries = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("Kobo Sync summary:")
    ]
    assert "suppressed_replay=1" in summaries[-1]


def test_control_real_last_modified_bump_is_still_delivered(sync_harness, monkeypatch):
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    first = sync_harness.sync()
    token = first.headers[sync_harness.token_header]

    _materialise_kepub(sync_harness)
    sync_harness.book.last_modified = datetime.now() + timedelta(seconds=5)
    sync_harness.session.commit()
    second = sync_harness.sync(token)

    assert len(_entitlements(second)) == 1


# ---------------------------------------------------------------- B1: restore


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    database.add(ub.Device(
        id=DEVICE_ID, user_id=USER_ID, kind="kobo", display_name="Libra",
        model="Kobo Libra Colour", active=True, created_by="auto",
    ))
    database.commit()
    yield database
    database.close()
    engine.dispose()


@pytest.fixture
def app(monkeypatch):
    application = Flask(__name__)
    monkeypatch.setattr(
        rs, "current_user",
        SimpleNamespace(
            id=USER_ID, name="reader", is_authenticated=True,
            kobo_two_way_annotation_sync=True,
        ),
    )
    monkeypatch.setattr(rs.config, "config_kobo_two_way_annotation_sync", True, raising=False)
    monkeypatch.setattr(
        "cps.services.kobo_annotation_stage0.schema_capable", lambda _e: True,
    )
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    authority.reset_skip_log_for_testing()
    return application


def _highlight(annotation_id, text, **overrides):
    values = {
        "user_id": USER_ID, "book_id": BOOK_ID, "annotation_id": annotation_id,
        "source": "kobo", "annotation_type": "highlight",
        "highlighted_text": text, "highlight_color": "#E8AFCF", "note_text": None,
        "content_id": f"{OWNED}!!Hellenistic Astrology - Chris Brennan.src_split_000.html",
        "chapter_progress": 0.15, "start_container_path": "span#kobo\\.130\\.8",
        "end_container_path": "span#kobo\\.130\\.8", "start_offset": 250,
        "end_offset": 329, "context_string": "",
        "client_modified_at": datetime(2026, 9, 9, 22, 5, 22), "hidden": False,
        "content_revision": 1,
    }
    values.update(overrides)
    return ub.Annotation(**values)


def _unseeded_owned(monkeypatch, session):
    book = SimpleNamespace(id=BOOK_ID, uuid=OWNED, title="Hellenistic Astrology", identifiers=[])
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _c: book)
    session.add(ub.KoboAnnotationBookState(
        user_id=USER_ID, book_id=BOOK_ID, content_id=OWNED,
        authority_status="unseeded", authority_revision=0,
        generation_id="00000000-0000-0000-0000-000000000567",
        opaque_content_status="unknown",
    ))
    session.commit()
    return book


def _get(app, device_id=DEVICE_ID):
    with app.test_request_context(f"/api/v3/content/{OWNED}/annotations?limit=100"):
        g.annotation_origin_device_id = device_id
        return rs.handle_annotations.__wrapped__(OWNED)


def test_download_route_records_a_pending_restore_for_the_device(session, monkeypatch, tmp_path):
    from cps import helper

    book = SimpleNamespace(
        id=BOOK_ID, title="Hellenistic Astrology", authors=[], path="x",
        data=[SimpleNamespace(name="b", format="KEPUB", uncompressed_size=1)],
    )
    data = book.data[0]
    monkeypatch.setattr(helper, "calibre_db", SimpleNamespace(
        get_filtered_book=lambda *_a, **_k: book,
        get_book_format=lambda *_a: data,
    ))
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(
        id=USER_ID, name="reader", is_authenticated=True, is_anonymous=False,
        role_admin=lambda: False,
    ))
    monkeypatch.setattr(helper.ub, "update_download", lambda *_a: None)
    monkeypatch.setattr(helper, "get_valid_filename", lambda name, **_k: name)
    monkeypatch.setattr(helper, "CWA_DB", lambda: SimpleNamespace(log_activity=lambda **_k: None))
    served = {}
    monkeypatch.setattr(
        helper, "do_download_file",
        lambda *a, **k: served.setdefault("called", True) and make_response(b"bytes"),
    )
    app = Flask(__name__)
    with app.test_request_context(f"/kobo/token/download/{BOOK_ID}/kepub"):
        g.annotation_origin_device_id = DEVICE_ID
        helper.get_download_link(BOOK_ID, "kepub", "kobo")

    assert served == {"called": True}
    row = session.query(ub.KoboDeviceBookDownload).one()
    assert (row.device_id, row.book_id, row.restore_state) == (DEVICE_ID, BOOK_ID, "pending")

    # a second download re-arms the row rather than duplicating it
    row.restore_state = "served"
    session.commit()
    with app.test_request_context(f"/kobo/token/download/{BOOK_ID}/kepub"):
        g.annotation_origin_device_id = DEVICE_ID
        helper.get_download_link(BOOK_ID, "kepub", "kobo")
    rows = session.query(ub.KoboDeviceBookDownload).all()
    assert [r.restore_state for r in rows] == ["pending"]


def test_first_get_after_download_is_answered_from_local_rows(app, session, monkeypatch):
    _unseeded_owned(monkeypatch, session)
    session.add_all([
        _highlight("f843c039-80ab-4022-be22-8415e6e2db33",
                   "these are techniques that can do things"),
        _highlight("c7d9ceab-71f1-4b80-8588-e7b8d75ce865",
                   "I believe that my perspective as a practicing astrologer",
                   start_container_path="span#kobo\\.132\\.3",
                   end_container_path="span#kobo\\.132\\.3"),
    ])
    session.commit()
    ledger.record_download(device_id=DEVICE_ID, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("the post-download GET went to Kobo's empty cloud set"),
    )

    response = _get(app)

    assert response.status_code == 200
    payload = json.loads(response.get_data())
    by_id = {a["id"]: a for a in payload["annotations"]}
    assert len(by_id) == 2
    assert by_id["c7d9ceab-71f1-4b80-8588-e7b8d75ce865"]["highlightedText"].startswith("I believe")
    assert by_id["f843c039-80ab-4022-be22-8415e6e2db33"]["location"]["span"]["startPath"] == "span#kobo\\.130\\.8"
    assert response.headers["ETag"].startswith('W/"CWNG:')

    state = session.query(ub.KoboAnnotationBookState).one()
    assert state.authority_status == "authoritative"
    assert state.ever_authoritative is True
    download = session.query(ub.KoboDeviceBookDownload).one()
    assert (download.restore_state, download.restored_count) == ("served", 2)

    # CWNG is the authority from now on: the next GET (no pending download)
    # is still answered locally with the same set.
    again = _get(app)
    assert again.status_code == 200
    assert json.loads(again.get_data()) == payload


def test_restore_is_scoped_to_the_device_that_downloaded(app, session, monkeypatch):
    _unseeded_owned(monkeypatch, session)
    session.add(_highlight("a-1", "some passage"))
    session.add(ub.Device(
        id=2, user_id=USER_ID, kind="kobo", display_name="Clara",
        model="Kobo Clara BW", active=True, created_by="auto",
    ))
    session.commit()
    ledger.record_download(device_id=2, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))
    upstream = b'{"annotations":[],"nextPageOffsetToken":null}'
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: make_response(upstream, 200, {"ETag": 'W/"0"'}),
    )

    response = _get(app, device_id=DEVICE_ID)

    assert response.get_data() == upstream
    assert session.query(ub.KoboDeviceBookDownload).one().restore_state == "pending"


def test_empty_local_set_after_download_still_proxies(app, session, monkeypatch):
    _unseeded_owned(monkeypatch, session)
    ledger.record_download(device_id=DEVICE_ID, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))
    upstream = b'{"annotations":[{"id":"cloud-only"}],"nextPageOffsetToken":null}'
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: make_response(upstream, 200, {"ETag": 'W/"kobo"'}),
    )

    response = _get(app)

    assert response.get_data() == upstream
    download = session.query(ub.KoboDeviceBookDownload).one()
    assert (download.restore_state, download.restored_count) == ("empty", 0)
    # the ordinary seed path ran from the upstream answer, not from our (empty) rows
    assert session.query(ub.Annotation).count() == 1


# ---------------------------------------------------------------- B2: re-anchor


def _kepub(path, chapters):
    """chapters: [(zip name, [(span id, text), ...])]."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(
            '<item id="c%d" href="%s" media-type="application/xhtml+xml"/>'
            % (i, os.path.relpath(name, "OEBPS")) for i, (name, _s) in enumerate(chapters)
        )
        refs = "".join('<itemref idref="c%d"/>' % i for i in range(len(chapters)))
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0"><manifest>%s</manifest><spine>%s</spine></package>'
                   % (items, refs))
        for name, spans in chapters:
            body = "".join(
                '<p><span class="koboSpan" id="%s">%s</span></p>' % (sid, text)
                for sid, text in spans
            )
            z.writestr(name, '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                       '<body>%s</body></html>' % body)


def test_rows_whose_chapter_vanished_are_reanchored_by_their_text(tmp_path):
    path = tmp_path / "book.kepub"
    _kepub(path, [
        ("OEBPS/chap0001.xhtml", [
            ("kobo.1.1", "CHAPTER ONE"),
            ("kobo.2.1", "The quick brown fox jumps over the lazy dog."),
        ]),
        ("OEBPS/chap0021.xhtml", [
            ("kobo.1.1", "CHAPTER TWENTY-ONE"),
            ("kobo.156.1", "In short, these are techniques that can do things that we "
                           "didn’t even think were possible, and the tradition"),
            ("kobo.156.2", " shows it. Repeated phrase here."),
            ("kobo.157.1", "Repeated phrase here."),
        ]),
    ])
    index = reanchor.KepubIndex(str(path))
    log = logging.getLogger("test")
    moved = _highlight("moved", "these are techniques that can do things that we didn't even think were possible")
    ambiguous = _highlight("ambiguous", "Repeated phrase here.")
    missing = _highlight("missing", "text that is nowhere in the book")
    stays = _highlight("stays", "The quick brown fox",
                       content_id=f"{OWNED}!!OEBPS/chap0001.xhtml",
                       start_container_path="span#kobo\\.2\\.1", start_offset=0,
                       end_container_path="span#kobo\\.2\\.1", end_offset=19)

    changed = reanchor.reanchor_rows(index, [moved, ambiguous, missing, stays], OWNED, log=log)

    assert [row.annotation_id for row in changed] == ["moved"]
    assert moved.content_id == f"{OWNED}!!OEBPS/chap0021.xhtml"
    assert moved.start_container_path == "span#kobo\\.156\\.1"
    assert moved.end_container_path == "span#kobo\\.156\\.1"
    span_text = ("In short, these are techniques that can do things that we "
                 "didn’t even think were possible, and the tradition")
    assert span_text[moved.start_offset:moved.end_offset] == (
        "these are techniques that can do things that we didn’t even think were possible"
    )
    assert 0 <= moved.chapter_progress < 0.5
    # untouched rows keep their (stale or valid) location; nothing is dropped
    assert ambiguous.content_id.endswith("src_split_000.html")
    assert missing.content_id.endswith("src_split_000.html")
    assert stays.start_offset == 0 and stays.content_id.endswith("chap0001.xhtml")


def test_reanchor_spans_a_span_boundary(tmp_path):
    path = tmp_path / "book.kepub"
    _kepub(path, [("OEBPS/c.xhtml", [
        ("kobo.1.1", "Alpha beta gamma "),
        ("kobo.1.2", "delta epsilon."),
    ])])
    row = _highlight("x", "gamma delta")

    reanchor.reanchor_rows(reanchor.KepubIndex(str(path)), [row], OWNED, log=logging.getLogger("t"))

    assert (row.start_container_path, row.start_offset) == ("span#kobo\\.1\\.1", 11)
    assert (row.end_container_path, row.end_offset) == ("span#kobo\\.1\\.2", 5)


def test_restore_get_reanchors_before_serving(app, session, monkeypatch, tmp_path):
    """End to end: stale row + pending download -> served with the new location."""
    book = _unseeded_owned(monkeypatch, session)
    path = tmp_path / "lib" / "Brennan" / "Hellenistic"
    path.mkdir(parents=True)
    _kepub(path / "hellenistic.kepub", [("OEBPS/chap0021.xhtml", [
        ("kobo.156.1", "these are techniques that can do things that we didn't even think were possible"),
    ])])
    book.path = os.path.join("Brennan", "Hellenistic")
    book.data = [SimpleNamespace(name="hellenistic", format="KEPUB")]
    monkeypatch.setattr(reanchor.config, "get_book_path", lambda: str(tmp_path / "lib"))
    session.add(_highlight("moved", "these are techniques that can do things that we didn't even think were possible"))
    session.commit()
    ledger.record_download(device_id=DEVICE_ID, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services",
                        lambda **_k: pytest.fail("proxied"))

    response = _get(app)

    [served] = json.loads(response.get_data())["annotations"]
    assert served["location"]["span"]["chapterFilename"] == "OEBPS/chap0021.xhtml"
    assert served["location"]["span"]["startPath"] == "span#kobo\\.156\\.1"
    assert served["location"]["span"]["startChar"] == 0
    stored = session.query(ub.Annotation).one()
    assert stored.content_id == f"{OWNED}!!OEBPS/chap0021.xhtml"
