# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral regressions from the independent My Library correctness review."""

from datetime import datetime
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from jinja2 import Environment, StrictUndefined
from sqlalchemy import create_engine, event, false, true
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _attached_session():
    engine = create_engine("sqlite://")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute(
            "ATTACH DATABASE ':memory:' AS calibre"
        ),
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _book(book_id, title, modified):
    book = db.Books(
        title, title, "Author", modified, db.Books.DEFAULT_PUBDATE,
        "1.0", modified, "book-%d" % book_id, 0, [], [],
    )
    book.id = book_id
    book.uuid = "book-uuid-%d" % book_id
    return book


def _delivered_ids(response):
    result = []
    for item in response.get_json():
        envelope = item.get("NewEntitlement") or item.get("ChangedEntitlement")
        if envelope and "BookMetadata" in envelope:
            result.append(int(envelope["BookEntitlement"]["Id"]))
    return result


def test_sync_all_delivers_old_books_after_membership_add_across_pages(monkeypatch):
    """Membership time is a lossless composite cursor across capped pages."""
    from cps import kobo as kobo_module, kobo_sync_status

    engine, session = _attached_session()
    old = datetime(2026, 1, 1, 12, 0, 0)
    cursor = datetime(2026, 2, 1, 12, 0, 0)
    added = datetime(2026, 3, 1, 12, 0, 0)
    already_synced = _book(1, "Already synced", old)
    newly_selected = [
        _book(book_id, "Selected later %d" % book_id, old)
        for book_id in (2, 3, 4)
    ]
    session.add_all([already_synced] + newly_selected)
    session.add_all([
        db.Data(1, "EPUB", 1, "one"),
        db.Data(2, "EPUB", 1, "two"),
        db.Data(3, "EPUB", 1, "three"),
        db.Data(4, "EPUB", 1, "four"),
        ub.UserLibraryBook(user_id=17, book_id=1, added_at=old),
        ub.UserLibraryBook(user_id=17, book_id=2, added_at=added),
        ub.UserLibraryBook(user_id=17, book_id=3, added_at=added),
        ub.UserLibraryBook(user_id=17, book_id=4, added_at=added),
        # Keep the real cursor intact: a completely empty tracking table is a
        # fresh-device signal and deliberately resets to datetime.min.
        ub.KoboSyncedBooks(user_id=17, book_id=1,
                           book_uuid=already_synced.uuid),
    ])
    session.commit()

    user = SimpleNamespace(
        id=17, name="membership-cursor", has_own_library=True,
        kobo_only_shelves_sync=False, role_download=lambda: True,
    )
    cdb = SimpleNamespace(
        session=session, reconnect_db=lambda *_a, **_kw: None,
        common_filters=lambda **_kw: true(),
    )
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *_a, **_kw: session.commit())
    monkeypatch.setattr(kobo_module, "calibre_db", cdb)
    monkeypatch.setattr(kobo_module, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo_module.config, "config_kobo_sync_magic_shelves", False, raising=False)
    monkeypatch.setattr(kobo_module, "get_download_url_for_book", lambda *_a: "/download")
    monkeypatch.setattr(kobo_module, "get_magic_shelf_book_ids_for_kobo", lambda _uid: (set(), True))
    monkeypatch.setattr(kobo_module, "get_magic_shelf_membership_added_at", lambda _uid: None)
    monkeypatch.setattr(kobo_module, "sync_shelves", lambda *_a, **_kw: None)
    monkeypatch.setattr(kobo_module, "create_book_entitlement",
                        lambda book, archived=False: {"Id": str(book.id), "IsRemoved": archived})
    monkeypatch.setattr(kobo_module, "get_metadata", lambda book: {"Id": str(book.id)})
    monkeypatch.setattr(kobo_module, "SYNC_ITEM_LIMIT", 2)

    token = kobo_module.SyncToken.SyncToken(
        books_last_created=cursor,
        books_last_modified=cursor,
        books_last_id=99,
    ).build_sync_token()
    app = Flask(__name__)
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    try:
        with app.test_request_context(
            "/v1/library/sync",
            headers={kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER: token},
        ):
            response = kobo_module.HandleSyncRequest.__wrapped__()
        assert _delivered_ids(response) == [2, 3]

        # The returned composite cursor must walk the remaining tie rather
        # than closing the added_at arm after only the first capped page.
        next_token = response.headers[kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER]
        with app.test_request_context(
            "/v1/library/sync",
            headers={kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER: next_token},
        ):
            second = kobo_module.HandleSyncRequest.__wrapped__()
        assert _delivered_ids(second) == [4]

        final_token = second.headers[kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER]
        with app.test_request_context(
            "/v1/library/sync",
            headers={kobo_module.SyncToken.SyncToken.SYNC_TOKEN_HEADER: final_token},
        ):
            third = kobo_module.HandleSyncRequest.__wrapped__()
        assert _delivered_ids(third) == []
    finally:
        session.close()
        engine.dispose()


def test_classic_shelf_order_get_renders_real_route_rows(monkeypatch):
    """Exercise the route's ORM row shape through the real template loop."""
    from cps import shelf as shelf_module

    engine, session = _attached_session()
    now = datetime(2026, 8, 28, 12, 0, 0)
    session.add(_book(1, "Visible title", now))
    stored_shelf = ub.Shelf(id=9, name="Order me", is_public=0, user_id=17)
    session.add(stored_shelf)
    stored_shelf.books.append(ub.BookShelf(book_id=1, order=1))
    session.commit()

    fake_user = SimpleNamespace(id=17, is_anonymous=False,
                                role_edit_shelfs=lambda: True)
    cdb = SimpleNamespace(session=session, common_filters=lambda: true())
    monkeypatch.setattr(shelf_module.ub, "session", session)
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    monkeypatch.setattr(shelf_module, "current_user", fake_user)
    monkeypatch.setattr(shelf_module, "_", lambda text, **values: text % values if values else text)

    template_source = (ROOT / "cps/templates/shelf_order.html").read_text()
    start = template_source.index("{% for entry in entries %}")
    end = template_source.index("{% endfor %}", start) + len("{% endfor %}")
    environment = Environment(undefined=StrictUndefined)
    environment.filters["shortentitle"] = lambda value, *_args: value
    environment.filters["formatfloat"] = lambda value, *_args: value
    loop = environment.from_string(template_source[start:end])

    def render_real_loop(_name, *, entries, **_context):
        return loop.render(
            entries=entries,
            image=SimpleNamespace(book_cover=lambda book: book.title),
            _=lambda text, **_kw: text,
        )

    monkeypatch.setattr(shelf_module, "render_title_template", render_real_loop)
    app = Flask(__name__)
    with app.test_request_context("/shelf/order/9"):
        rendered = inspect.unwrap(shelf_module.order_shelf)(9)
    assert "Visible title" in rendered
    session.close()
    engine.dispose()


def test_change_shelf_order_renumbers_every_stored_row(monkeypatch):
    """A viewer restriction cannot leave duplicate/stale stored positions."""
    from cps import shelf as shelf_module

    engine, session = _attached_session()
    now = datetime(2026, 8, 28, 12, 0, 0)
    session.add_all([_book(1, "C", now), _book(2, "A", now),
                     _book(3, "B", now)])
    stored_shelf = ub.Shelf(id=9, name="Shared", is_public=1, user_id=18)
    session.add(stored_shelf)
    for book_id, order in ((1, 0), (2, 1), (3, 2)):
        stored_shelf.books.append(ub.BookShelf(book_id=book_id, order=order))
    session.commit()
    cdb = SimpleNamespace(
        session=session,
        # Model the personal-mode viewer not owning book 2.
        common_filters=lambda: db.Books.id != 2,
    )
    monkeypatch.setattr(shelf_module.ub, "session", session)
    monkeypatch.setattr(shelf_module.ub, "session_commit", lambda *_a, **_kw: session.commit())
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)

    shelf_module.change_shelf_order(9, [db.Books.title.asc()])
    rows = (session.query(ub.BookShelf).filter_by(shelf=9)
            .order_by(ub.BookShelf.order).all())
    assert [(row.book_id, row.order) for row in rows] == [
        (2, 0), (3, 1), (1, 2),
    ]
    session.close()
    engine.dispose()


def test_shelf_gesture_adds_membership_before_shelf_request():
    source = (ROOT / "frontend/src/components/AddToShelf.tsx").read_text()
    assert "useAddToMyLibrary" in source
    assert "addToLibrary.mutateAsync(bookId)" in source
    assert source.index("addToLibrary.mutateAsync(bookId)") < source.index("add.mutateAsync")
    assert "Could not add the book. Please try again." in source
    assert "Could not add this book to the shelf." in source
    assert "triggerRef.current?.focus()" in source


def test_global_browser_can_hydrate_unowned_book_detail():
    """The non-member detail route is the entry point for both add gestures."""
    source = (ROOT / "cps/api/books.py").read_text()
    start = source.index("def book_detail(book_id):")
    end = source.index("\n\n@api_v1.route", start)
    detail = source[start:end]
    assert "allow_show_global = _can_browse_global()" in detail
    assert "allow_show_global=allow_show_global" in detail
    assert 'body["in_my_library"]' in detail


def test_classic_managed_removal_redirects_to_accessible_library():
    source = (ROOT / "cps/templates/detail.html").read_text()
    success = source.split("remove-from-my-library-confirm", 1)[1].split("error:", 1)[0]
    assert "role_browse_global" in success
    assert "url_for('web.index')" in success


def test_admin_can_add_one_book_to_a_named_users_library(monkeypatch):
    from cps.api import admin as admin_api
    from cps import user_library

    endpoint = getattr(admin_api, "admin_add_book_to_user_library", None)
    assert callable(endpoint)

    engine, session = _attached_session()
    now = datetime(2026, 8, 28, 12, 0, 0)
    session.add(_book(41, "Managed addition", now))
    target = ub.User(
        name="managed", email="managed@example.invalid", password="",
        role=constants.ROLE_USER, has_own_library=True,
        user_library_seeded=True, default_language="all",
    )
    session.add(target)
    session.commit()
    cdb = SimpleNamespace(session=session, common_filters=lambda **_kw: true())
    monkeypatch.setattr(admin_api.ub, "session", session)
    monkeypatch.setattr(user_library, "calibre_db", cdb)
    monkeypatch.setattr(admin_api, "_require_admin", lambda: None)

    app = Flask(__name__)
    with app.test_request_context(
        "/api/v1/admin/users/%d/my-library/41" % target.id,
        method="PUT",
    ):
        response = inspect.unwrap(endpoint)(target.id, 41)
    payload = response.get_json()
    assert payload["membership_count"] == 1
    assert payload["book_title"] == "Managed addition"
    assert session.query(ub.UserLibraryBook).filter_by(
        user_id=target.id, book_id=41,
    ).count() == 1
    session.close()
    engine.dispose()


def test_removed_book_keeps_title_in_kobo_two_way_state(monkeypatch):
    """Trailing annotation state stays resolvable outside membership."""
    from cps.api import kobo_two_way

    engine, session = _attached_session()
    now = datetime(2026, 8, 28, 12, 0, 0)
    session.add(_book(51, "Remembered title", now))
    session.commit()
    cdb = SimpleNamespace(
        session=session,
        # A removed personal-library book fails the normal visibility policy.
        common_filters=lambda **_kw: false(),
    )
    monkeypatch.setattr(kobo_two_way, "calibre_db", cdb)
    assert kobo_two_way._book_titles([51]) == {51: "Remembered title"}
    session.close()
    engine.dispose()
