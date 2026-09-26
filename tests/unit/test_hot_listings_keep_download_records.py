# SPDX-License-Identifier: GPL-3.0-or-later
"""Book listings read every account's download records and delete none.

A download record is its account's history, and the Kobo upgrade audit reads
it as proof that the account took delivery of a book.  The classic and OPDS
Hot Books lists deleted every account's records for each listed book their
viewer could not see (#2207's real-upgrade gate, limit (c): one OPDS view by
an account restricted to one tag deleted 239 records of 48 books).  The one
record a listing may remove is a download of a book the library deleted.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub
from cps.sort_orders import BOOK_SORT_ORDERS

pytestmark = pytest.mark.unit

MYSTERY_BOOKS = (1, 3, 6)
SCIENCE_BOOKS = (2, 4)
DELETED_BOOK = 5        # below the library's highest id and no longer in it
UNKNOWN_BOOK = 7        # above the library's highest id: not a deletion
DOWNLOADS = {
    "reader1": (1, 2, 3, 4, DELETED_BOOK, UNKNOWN_BOOK),
    "reader2": (1, 2, DELETED_BOOK),
    "kid": (3,),
}


@pytest.fixture
def world(monkeypatch):
    from cps import app, config, helper, opds, web
    from cps.api import books as books_api

    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    app_session = sessionmaker(bind=engine)()
    metadata_session = sessionmaker(bind=engine)()

    tags = {"Mystery": db.Tags("Mystery"), "Science": db.Tags("Science")}
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for tag, book_ids in (("Mystery", MYSTERY_BOOKS), ("Science", SCIENCE_BOOKS)):
        for book_id in book_ids:
            book = db.Books("Book %d" % book_id, "Book %d" % book_id, "Author", now,
                            db.Books.DEFAULT_PUBDATE, "1.0", now, "book-%d" % book_id,
                            0, [], [])
            book.id = book_id
            book.tags.append(tags[tag])
            metadata_session.add(book)
    metadata_session.commit()

    users = {}
    for name, allowed_tags in (("reader1", ""), ("reader2", ""), ("kid", "Mystery")):
        users[name] = ub.User(
            name=name, email="%s@example.invalid" % name, password="",
            role=constants.ROLE_DOWNLOAD, default_language="all",
            sidebar_view=constants.SIDEBAR_HOT | constants.SIDEBAR_DOWNLOAD,
            allowed_tags=allowed_tags, denied_tags="")
        app_session.add(users[name])
    app_session.flush()
    for name, book_ids in DOWNLOADS.items():
        app_session.add_all([ub.Downloads(user_id=users[name].id, book_id=book_id)
                             for book_id in book_ids])
    # reader2 removed book 1 from a Kobo, which archives it for reader2.
    app_session.add(ub.ArchivedBook(user_id=users["reader2"].id, book_id=1,
                                    is_archived=True))
    app_session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(config_restricted_column=0, config_random_books=0,
                                 config_books_per_page=20)
    monkeypatch.setattr(ub, "session", app_session)
    for module in (helper, web, opds, books_api):
        monkeypatch.setattr(module, "calibre_db", cdb)
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(config, "config_random_books", 0, raising=False)
    monkeypatch.setattr(config, "config_books_per_page", 20, raising=False)

    def rendered(_template, **kwargs):
        return kwargs
    monkeypatch.setattr(web, "render_title_template", rendered)
    monkeypatch.setattr(web, "_", lambda message, **values: message % values)
    monkeypatch.setattr(opds, "render_xml_template", rendered)
    monkeypatch.setattr(books_api, "_rows_to_items",
                        lambda entries, *_args: [row.Books.id for row in entries])

    def as_viewer(name):
        viewer = users[name]
        for module in (db, web, books_api):
            monkeypatch.setattr(module, "current_user", viewer)
        monkeypatch.setattr(opds.auth, "current_user", lambda: viewer)
        return viewer

    def listed(view, name, *, page=1, per_page=20):
        """Book ids one page of ``view`` shows ``name``, and its total."""
        viewer = as_viewer(name)
        monkeypatch.setattr(config, "config_books_per_page", per_page, raising=False)
        cdb.config.config_books_per_page = per_page
        offset = (page - 1) * per_page
        if view == "classic":
            with app.test_request_context("/hot"):
                page_ = web.render_hot_books(page, (BOOK_SORT_ORDERS["hotdesc"], "hotdesc"))
            return [row.Books.id for row in page_["entries"]], page_["pagination"]
        if view == "opds":
            with app.test_request_context("/opds/hot?offset=%d" % offset):
                opds.g.allow_anonymous = False
                page_ = opds.feed_hot.__wrapped__()
            return [row.Books.id for row in page_["entries"]], page_["pagination"]
        if view == "downloaded":
            with app.test_request_context("/downloaded"):
                page_ = web.render_downloaded_books(page, ([db.Books.id.desc()], "new"),
                                                    viewer.id)
            return [row.Books.id for row in page_["entries"]], page_["pagination"]
        assert view == "spa"
        with app.test_request_context(
                "/api/v1/books?filter=hot&page=%d&per_page=%d" % (page, per_page)):
            body = books_api.list_books.__wrapped__().get_json()
        return body["items"], SimpleNamespace(total_count=body["total"])

    def records():
        return sorted((row.user_id, row.book_id) for row in app_session.query(ub.Downloads))

    yield SimpleNamespace(users=users, listed=listed, records=records)

    metadata_session.close()
    app_session.close()
    engine.dispose()


def _without_deleted_book(world):
    return sorted((world.users[name].id, book_id)
                  for name, book_ids in DOWNLOADS.items()
                  for book_id in book_ids if book_id != DELETED_BOOK)


@pytest.mark.parametrize("view", ["classic", "opds", "spa"])
@pytest.mark.parametrize("viewer, visible", [
    # Tag-restricted: sees only Mystery books, most downloaded first.
    ("kid", [3, 1]),
    # Archived book 1 for itself: every other account still holds it.
    ("reader2", [3, 2, 4]),
])
def test_hot_books_deletes_no_record_of_a_book_still_in_the_library(
        world, view, viewer, visible):
    shown, pagination = world.listed(view, viewer)

    assert shown == visible
    assert pagination.total_count == len(visible)
    assert world.records() == _without_deleted_book(world)


@pytest.mark.parametrize("view", ["classic", "opds", "spa"])
def test_hot_books_pages_through_the_books_its_viewer_can_see(world, view):
    first, pagination = world.listed(view, "kid", per_page=1)
    second, _ = world.listed(view, "kid", page=2, per_page=1)

    assert (first, second) == ([3], [1])
    assert pagination.total_count == 2
    if view != "spa":
        assert pagination.has_next


@pytest.mark.parametrize("viewer", ["kid", "reader2"])
def test_downloaded_books_lists_the_viewers_books_and_deletes_nothing(world, viewer):
    before = world.records()

    shown, _pagination = world.listed("downloaded", viewer)

    assert shown == {"kid": [3], "reader2": [2]}[viewer]
    assert world.records() == before


def test_hot_books_keeps_hotness_order_across_id_batches(world, monkeypatch):
    """Ids cross from app.db to the library in bounded batches (#937); a batch
    boundary must neither reorder nor drop a book."""
    from cps import helper

    batches = []
    real = helper._present_and_visible_book_ids

    def recording(book_ids, visibility_filter):
        batches.append(list(book_ids))
        return real(book_ids, visibility_filter)

    monkeypatch.setattr(helper, "SQLITE_IN_CHUNK_SIZE", 2)
    monkeypatch.setattr(helper, "_present_and_visible_book_ids", recording)

    shown, _pagination = world.listed("classic", "reader1")

    assert [len(batch) for batch in batches] == [2, 2, 2]
    assert shown == [3, 2, 1, 4]
    assert shown != sorted(shown)  # hotness order is not id order
