# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public-shelf listing exception for personal-library users (issue #1939)."""

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub


pytestmark = pytest.mark.unit
REPO_ROOT = Path(__file__).resolve().parents[2]


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
    )
    public_shelf = ub.Shelf(name="Shared", user_id=99, is_public=1)
    private_shelf = ub.Shelf(name="Private", user_id=1, is_public=0)
    app_session.add_all([user, public_shelf, private_shelf])
    app_session.flush()
    public_shelf.books.append(ub.BookShelf(book_id=2, order=1))
    private_shelf.user_id = user.id
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
    user, public_shelf, _private_shelf = _seed_library(app_session, metadata_session)

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(
        config_restricted_column=0,
        config_books_per_page=20,
        config_random_books=0,
    )
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)

    yield app_session, cdb, user, public_shelf

    metadata_session.close()
    app_session.close()
    engine.dispose()


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

    app_session, cdb, user, public_shelf = opds_library
    assert public_shelf.user_id != user.id
    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=2,
    ).count() == 0

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


class _PublicShelfOptInVisitor(ast.NodeVisitor):
    def __init__(self, module):
        self.module = module
        self.function = None
        self.calls = []

    def visit_FunctionDef(self, node):
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        for keyword in node.keywords:
            if keyword.arg == "allow_public_shelf_books":
                self.calls.append(
                    (self.module, self.function, node.lineno, keyword.value)
                )
        self.generic_visit(node)


def _public_shelf_keyword_calls():
    calls = []
    for path in (REPO_ROOT / "cps").rglob("*.py"):
        module = path.relative_to(REPO_ROOT).as_posix()
        visitor = _PublicShelfOptInVisitor(module)
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))
        calls.extend(visitor.calls)
    return calls


def _is_public_shelf_predicate(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bool"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Attribute)
        and node.args[0].attr == "is_public"
        and isinstance(node.args[0].value, ast.Name)
        and node.args[0].value.id == "shelf"
    )


def test_only_public_shelf_listing_paths_opt_in():
    calls = _public_shelf_keyword_calls()
    forwarding_calls = [
        call for call in calls
        if isinstance(call[3], ast.Name)
        and call[3].id == "allow_public_shelf_books"
    ]
    opt_in_calls = [call for call in calls if call not in forwarding_calls]

    # db.py only carries the caller's decision through the canonical filter.
    # Every actual opt-in must be one of these read-listing surfaces, and every
    # one must derive the exception from the selected shelf's persisted policy.
    assert {(module, function) for module, function, _line, _value in forwarding_calls} == {
        ("cps/db.py", "fill_indexpage_with_archived_books"),
    }
    assert {(module, function) for module, function, _line, _value in opt_in_calls} == {
        ("cps/shelf.py", "render_show_shelf"),
        ("cps/shelf.py", "_shelf_book_count"),
        ("cps/api/shelves.py", "shelf_detail"),
        ("cps/opds.py", "feed_shelf"),
    }
    assert all(_is_public_shelf_predicate(value) for *_caller, value in opt_in_calls)
    assert {module for module, *_rest in opt_in_calls} == {
        "cps/shelf.py",
        "cps/api/shelves.py",
        "cps/opds.py",
    }
    assert "cps/kobo.py" not in {module for module, *_rest in opt_in_calls}
