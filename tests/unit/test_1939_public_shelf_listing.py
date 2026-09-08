# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public-shelf listing exception for personal-library users (issue #1939)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub


pytestmark = pytest.mark.unit


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(
        title, title, "Author", now, db.Books.DEFAULT_PUBDATE,
        "1.0", now, "public-shelf-%d" % book_id, 0, [], [],
    )
    book.id = book_id
    return book


def _seed_library(app_session, metadata_session):
    metadata_session.add_all([
        _book(1, "Member book"),
        _book(2, "Public shelf book"),
        _book(3, "Private shelf book"),
    ])
    metadata_session.commit()

    user = ub.User(
        name="shelf-viewer",
        email="shelf-viewer@example.invalid",
        password="",
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
        sidebar_view=0,
    )
    public_shelf = ub.Shelf(name="Shared", user_id=99, is_public=1)
    private_shelf = ub.Shelf(name="Private", user_id=99, is_public=0)
    app_session.add_all([user, public_shelf, private_shelf])
    app_session.flush()
    public_shelf.books.append(ub.BookShelf(book_id=2, order=1))
    private_shelf.books.append(ub.BookShelf(book_id=3, order=1))
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    return user, public_shelf, private_shelf


@pytest.fixture
def library(monkeypatch):
    app_engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(app_engine)
    app_session = sessionmaker(bind=app_engine)()

    metadata_engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(metadata_engine)
    metadata_session = sessionmaker(bind=metadata_engine)()
    user, public_shelf, private_shelf = _seed_library(app_session, metadata_session)

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(config_restricted_column=0)
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)

    yield app_session, metadata_session, cdb, user, public_shelf, private_shelf

    metadata_session.close()
    app_session.close()
    metadata_engine.dispose()
    app_engine.dispose()


@pytest.fixture
def opds_library(monkeypatch):
    # OPDS's shelf query joins the app-db BookShelf table to calibre Books.
    # Put both schemas in one in-memory SQLite database so this unit test can
    # execute that production query without relying on a configured library.
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    app_session = sessionmaker(bind=engine)()
    metadata_session = sessionmaker(bind=engine)()
    user, public_shelf, private_shelf = _seed_library(app_session, metadata_session)

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(
        config_restricted_column=0,
        config_books_per_page=20,
        config_random_books=0,
    )
    monkeypatch.setattr(db.ub, "session", app_session)
    # Route tests let db.current_user resolve through the real LoginManager.

    yield app_session, cdb, user, public_shelf, private_shelf

    metadata_session.close()
    app_session.close()
    engine.dispose()


@pytest.fixture
def shelf_route_client(opds_library, monkeypatch):
    """Real shelf routes, login state, authorization, and database filters."""
    from cps import config
    from cps import shelf as shelf_module
    from cps.api import api_v1
    from cps.api import books as books_api
    from cps.api import shelves as shelves_api
    from cps.cw_login import LoginManager

    app_session, cdb, user, public_shelf, private_shelf = opds_library

    app = flask.Flask(__name__)
    app.config.update(SECRET_KEY="test", SESSION_PROTECTION=None)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(user_id, _random, _session_key):
        return app_session.get(ub.User, int(user_id))

    app.register_blueprint(api_v1)
    app.register_blueprint(shelf_module.shelf)

    monkeypatch.setattr(shelves_api, "calibre_db", cdb)
    monkeypatch.setattr(books_api, "calibre_db", cdb)
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    monkeypatch.setattr(config, "config_anonbrowse", 0, raising=False)
    monkeypatch.setattr(
        config, "config_allow_reverse_proxy_header_login", False, raising=False
    )
    monkeypatch.setattr(config, "config_books_per_page", 20, raising=False)
    monkeypatch.setattr(config, "config_random_books", 0, raising=False)
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)

    def _id_items(entries, *_args, **_kwargs):
        # The list-item serializer fans out into covers and reading-progress
        # services that are unrelated to shelf visibility.  Keep the real
        # route/query result and reduce only that rendering boundary to IDs.
        return [
            {"id": getattr(entry, "Books", entry).id}
            for entry in entries
        ]

    monkeypatch.setattr(shelves_api, "_rows_to_items", _id_items)
    monkeypatch.setattr(books_api, "_rows_to_items", _id_items)

    def _render_shelf(_template, **kwargs):
        # Jinja/Babel need the production localization/theme stack, which this
        # in-process route harness deliberately does not boot.  Only rendering
        # is stubbed; authorization and fill_indexpage's entries stay real.
        return flask.jsonify({"items": _id_items(kwargs["entries"])})

    monkeypatch.setattr(shelf_module, "render_title_template", _render_shelf)
    monkeypatch.setattr(
        shelf_module, "_", lambda message, **values: message % values
    )

    client = app.test_client()
    with client.session_transaction() as session:
        # Exercise the real LoginManager loader and both route decorators on
        # every request, using the persisted non-owner user seeded above.
        session["_user_id"] = str(user.id)
        session["_random"] = "route-test-random"
        session["_id"] = "route-test-session"
        session["_fresh"] = True

    return client, user, public_shelf, private_shelf


def _visible_ids(metadata_session, cdb, **filter_options):
    return [book.id for book in (
        metadata_session.query(db.Books)
        .filter(cdb.common_filters(**filter_options))
        .order_by(db.Books.id).all()
    )]


def test_public_shelf_lists_out_of_set_book(library, monkeypatch):
    from cps import shelf as shelf_module

    app_session, metadata_session, cdb, user, public_shelf, _private = library
    visible = (metadata_session.query(db.Books.id)
               .filter(db.Books.id == 2)
               .filter(cdb.common_filters(allow_public_shelf_books=True)).all())
    assert [row.id for row in visible] == [2]

    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    assert shelf_module._shelf_book_count(public_shelf, user) == 1
    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=2,
    ).count() == 0


def test_private_shelf_keeps_out_of_set_book_hidden(library, monkeypatch):
    from cps import shelf as shelf_module

    _app, metadata_session, cdb, user, _public, private_shelf = library
    visible = (metadata_session.query(db.Books.id)
               .filter(db.Books.id == 3)
               .filter(cdb.common_filters(
                   allow_public_shelf_books=bool(private_shelf.is_public)
               )).all())
    assert visible == []

    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    assert shelf_module._shelf_book_count(private_shelf, user) == 0


def test_ordinary_browse_stays_membership_scoped(library):
    _app, metadata_session, cdb, _user, _public, _private = library
    assert _visible_ids(metadata_session, cdb) == [1]


def test_spa_public_shelf_detail_lists_out_of_membership_book(
    shelf_route_client,
):
    client, user, public_shelf, _private_shelf = shelf_route_client
    assert public_shelf.user_id != user.id

    ordinary = client.get("/api/v1/books")
    assert ordinary.status_code == 200
    assert [item["id"] for item in ordinary.get_json()["items"]] == [1]

    response = client.get(f"/api/v1/shelves/{public_shelf.id}")
    assert response.status_code == 200
    assert [item["id"] for item in response.get_json()["items"]] == [2]


def test_spa_foreign_private_shelf_detail_is_forbidden(shelf_route_client):
    client, user, _public_shelf, private_shelf = shelf_route_client
    assert private_shelf.user_id != user.id

    response = client.get(f"/api/v1/shelves/{private_shelf.id}")

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"


def test_classic_public_shelf_render_lists_out_of_membership_book(
    shelf_route_client,
):
    client, user, public_shelf, _private_shelf = shelf_route_client
    assert public_shelf.user_id != user.id

    response = client.get(f"/shelf/{public_shelf.id}")

    assert response.status_code == 200
    assert [item["id"] for item in response.get_json()["items"]] == [2]


def test_viewing_public_shelf_does_not_create_membership(library):
    app_session, metadata_session, cdb, user, _public, _private = library
    before = app_session.query(ub.UserLibraryBook).filter_by(user_id=user.id).count()
    assert 2 in _visible_ids(
        metadata_session, cdb, allow_public_shelf_books=True
    )
    after = app_session.query(ub.UserLibraryBook).filter_by(user_id=user.id).count()
    assert (before, after) == (1, 1)


def test_opds_public_shelf_feed_lists_out_of_membership_book(
    opds_library, monkeypatch,
):
    from cps import app, opds

    app_session, cdb, user, public_shelf, _private_shelf = opds_library
    assert public_shelf.user_id != user.id
    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=2,
    ).count() == 0
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(opds, "calibre_db", cdb)
    monkeypatch.setattr(opds.auth, "current_user", lambda: user)
    monkeypatch.setattr(
        opds, "render_xml_template", lambda *_args, **kwargs: kwargs["entries"]
    )
    monkeypatch.setattr(opds.config, "config_books_per_page", 20, raising=False)
    monkeypatch.setattr(opds.config, "config_read_column", 0, raising=False)

    with app.test_request_context(f"/opds/shelf/{public_shelf.id}"):
        opds.g.allow_anonymous = False
        feed_entries = opds.feed_shelf.__wrapped__(public_shelf.id)

    assert [entry.Books.id for entry in feed_entries] == [2]


# The former AST allowlist here asserted that only listing routes could opt in.
# Shared-shelf continuation intentionally authorizes detail/read/download now.
# Real SQL and route isolation coverage above and in
# test_shared_book_continuation.py exercises that policy directly.
