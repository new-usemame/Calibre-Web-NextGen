# Calibre-Web-NextGen
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2849: OPDS entries carried nothing about the reader who asked.

A catalog app could show a read tick only by fetching each read-list feed
separately, and could get a book's numeric id only by parsing it out of the
acquisition URL. Each entry now carries ``<calibre:id>`` and, for a signed-in
reader, ``<cwng:read_status>`` with the same five names the API uses. These
tests play the app: fetch feeds through the real blueprint and the real
``feed.xml`` and read those elements back.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import db, opds, ub
from tests.unit.opds_feed_world import opds_route_app

pytestmark = pytest.mark.unit

ATOM = "{http://www.w3.org/2005/Atom}"
CALIBRE = "{http://calibre.kovidgoyal.net/2009/metadata}"
CWNG = "{urn:cwng:opds:1}"
READER_ID = 5
OTHER_ID = 6

# book id -> this reader's stored status (None: no row at all)
STATUSES = {
    1: ub.ReadBook.STATUS_FINISHED,
    2: ub.ReadBook.STATUS_IN_PROGRESS,
    3: ub.ReadBook.STATUS_DID_NOT_FINISH,
    4: ub.ReadBook.STATUS_ON_HOLD,
    5: None,
    6: ub.ReadBook.STATUS_UNREAD,
}
EXPECTED = {1: "finished", 2: "in_progress", 3: "did_not_finish", 4: "on_hold",
            5: "unread", 6: "unread"}
FEEDS = ["/opds/new", "/opds/search/book"]


def _reader(anonymous):
    return SimpleNamespace(
        id=READER_ID, is_anonymous=anonymous, is_authenticated=not anonymous,
        opds_only_shelves_sync=0, has_own_library=False,
        filter_language=lambda: "all", list_allowed_tags=lambda: [""],
        list_denied_tags=lambda: [""], allowed_column_value="", denied_column_value="",
        locale="en", role_download=lambda: True,
        check_visibility=lambda _value: True, show_detail_random=lambda: False,
    )


def _world(monkeypatch, anonymous):
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    event.listen(engine, "connect", db._register_sqlite_udfs)
    db.Books.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    created = datetime.now(timezone.utc)
    for book_id in STATUSES:
        title = "Book %d" % book_id
        book = db.Books(title, title, "Author", created, created, "1.0", created,
                        "uuid-2849-{}".format(book_id), 0, [], [])
        book.id = book_id
        session.add(book)
        session.add(db.Data(book_id, "EPUB", 100, title))
    session.commit()
    for book_id, status in STATUSES.items():
        if status is not None:
            session.add(ub.ReadBook(user_id=READER_ID, book_id=book_id, read_status=status))
    # Another account finished book 5: that must not show for this reader.
    session.add(ub.ReadBook(user_id=OTHER_ID, book_id=5,
                            read_status=ub.ReadBook.STATUS_FINISHED))
    session.commit()

    reader = _reader(anonymous)
    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    config = SimpleNamespace(
        config_read_column=0, config_books_per_page=20,
        config_calibre_web_title="Test", config_restricted_column=0,
    )
    cdb.config = config  # fill_indexpage (/opds/new) pages with its own config
    cdb.get_cc_columns = lambda *_args, **_kwargs: []
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(db, "current_user", reader)
    monkeypatch.setattr(opds, "calibre_db", cdb)
    monkeypatch.setattr(opds, "config", config)
    monkeypatch.setattr(opds.auth, "current_user", lambda: reader)
    return opds_route_app().test_client(), session, engine


@pytest.fixture
def signed_in(monkeypatch):
    client, session, engine = _world(monkeypatch, anonymous=False)
    try:
        yield client
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def guest(monkeypatch):
    client, session, engine = _world(monkeypatch, anonymous=True)
    try:
        yield client
    finally:
        session.close()
        engine.dispose()


def _entries(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.data[:300])
    entries = list(ET.fromstring(resp.data).iter(ATOM + "entry"))
    assert entries, "feed %s rendered no book entries" % url
    return entries


def _book_id_from_acquisition(entry):
    for link in entry.iter(ATOM + "link"):
        if link.get("rel") == "http://opds-spec.org/acquisition":
            return int(link.get("href").split("/opds/download/")[1].split("/")[0])
    raise AssertionError("entry has no acquisition link")


@pytest.mark.parametrize("url", FEEDS)
def test_each_entry_names_its_numeric_book_id(signed_in, url):
    entries = _entries(signed_in, url)
    for entry in entries:
        assert entry.findtext(CALIBRE + "id") == str(_book_id_from_acquisition(entry))
    assert len(entries) == len(STATUSES)


@pytest.mark.parametrize("url", FEEDS)
def test_each_entry_carries_this_readers_own_status(signed_in, url):
    seen = {}
    for entry in _entries(signed_in, url):
        statuses = entry.findall(CWNG + "read_status")
        assert len(statuses) == 1, ET.tostring(entry)[:400]
        seen[_book_id_from_acquisition(entry)] = statuses[0].text
    assert seen == EXPECTED


@pytest.mark.parametrize("url", FEEDS)
def test_a_guest_gets_the_id_but_no_personal_status(guest, url):
    for entry in _entries(guest, url):
        assert entry.findtext(CALIBRE + "id") == str(_book_id_from_acquisition(entry))
        assert entry.find(CWNG + "read_status") is None
