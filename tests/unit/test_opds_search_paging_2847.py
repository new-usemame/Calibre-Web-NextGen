# Calibre-Web-NextGen
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2847: an OPDS search returned every match in one feed.

Every other OPDS list feed pages at ``books_per_page`` and links the next page
with ``rel="next"``. The search feed loaded all matches and rendered them as a
single page, so a broad query on a large library sent the whole library as one
Atom document. These tests play an OPDS client: run a search through the real
blueprint and the real ``feed.xml``, follow ``rel="next"`` until it stops, and
check what came back on each page.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import db, opds, ub
from tests.unit.opds_feed_world import opds_route_app

pytestmark = pytest.mark.unit

ATOM = "{http://www.w3.org/2005/Atom}"
PER_PAGE = 3
TITLES = ["Book %02d" % n for n in range(1, 8)]  # 7 matches: pages of 3, 3, 1


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    event.listen(engine, "connect", db._register_sqlite_udfs)
    db.Books.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    created = datetime.now(timezone.utc)
    # Insert out of title order so the order the feed pages in has to come
    # from the query's ORDER BY, not from row ids.
    for book_id, title in enumerate(reversed(TITLES + ["Unrelated"]), 1):
        book = db.Books(title, title, "Author", created, created, "1.0", created,
                        "uuid-2847-{}".format(book_id), 0, [], [])
        book.id = book_id
        session.add(book)
        # Two files per book: search_query eager-joins them, so a LIMIT that
        # counted joined rows instead of books would short every page.
        session.add_all([db.Data(book_id, "EPUB", 100, title), db.Data(book_id, "PDF", 100, title)])
    session.commit()

    reader = SimpleNamespace(
        id=5, is_anonymous=False, opds_only_shelves_sync=0, has_own_library=False,
        filter_language=lambda: "all", list_allowed_tags=lambda: [""],
        list_denied_tags=lambda: [""], allowed_column_value="", denied_column_value="",
        locale="en", role_download=lambda: True,
        check_visibility=lambda _value: True,
    )
    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_restricted_column=0)
    cdb.get_cc_columns = lambda *_args, **_kwargs: []
    config = SimpleNamespace(
        config_read_column=0, config_books_per_page=PER_PAGE,
        config_calibre_web_title="Test", config_restricted_column=0,
    )
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(db, "current_user", reader)
    monkeypatch.setattr(opds, "calibre_db", cdb)
    monkeypatch.setattr(opds, "config", config)
    monkeypatch.setattr(opds.auth, "current_user", lambda: reader)
    app = opds_route_app()
    try:
        yield app.test_client()
    finally:
        session.close()
        engine.dispose()


def _page(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.data[:300])
    doc = ET.fromstring(resp.data)
    titles = [e.findtext(ATOM + "title") for e in doc.iter(ATOM + "entry")]
    links = {ln.get("rel"): ln.get("href") for ln in doc.iter(ATOM + "link")}
    return titles, links


def _walk(client, first_url):
    """Follow rel=next from ``first_url`` the way a reader app does."""
    pages, url = [], first_url
    while url is not None:
        assert len(pages) < 10, "rel=next never stopped"
        titles, links = _page(client, url)
        pages.append((url, titles, links))
        url = links.get("next")
    return pages


@pytest.mark.parametrize("first_url", [
    "/opds/search?query=book",
    "/opds/search/book",
    "/opds/search?query=book&lang=hu",
], ids=["query-form", "path-form", "query-form-with-lang"])
def test_search_feed_pages_at_books_per_page_and_links_every_page(world, first_url):
    pages = _walk(world, first_url)

    assert [len(titles) for _url, titles, _links in pages] == [3, 3, 1]
    walked = [t for _url, titles, _links in pages for t in titles]
    assert walked == TITLES, "every match exactly once, in title order"

    first_links, last_links = pages[0][2], pages[-1][2]
    assert "previous" not in first_links
    assert "previous" in pages[1][2] and "previous" in last_links
    for url, _titles, _links in pages[1:]:
        # The next page stays on the route and query the client started with.
        assert urlsplit(url).path == urlsplit(first_url).path, url
        if "lang=hu" in first_url:
            assert "lang=hu" in url, url


def test_previous_link_returns_to_the_page_before(world):
    titles_p2, links_p2 = _page(world, "/opds/search?query=book&offset=3")
    assert titles_p2 == TITLES[3:6]
    titles_p1, _ = _page(world, links_p2["previous"])
    assert titles_p1 == TITLES[0:3]


def test_a_search_with_no_matches_is_an_empty_feed_without_paging(world):
    titles, links = _page(world, "/opds/search?query=nomatch")
    assert titles == []
    assert "next" not in links and "previous" not in links


@pytest.mark.parametrize("offset", ["abc", "-3"])
def test_a_malformed_offset_serves_the_first_page(world, offset):
    titles, links = _page(world, "/opds/search?query=book&offset=" + offset)
    assert titles == TITLES[0:3]
    assert "next" in links
