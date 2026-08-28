# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contract for per-user library membership (issue #1939)."""

from datetime import datetime, timezone
import inspect as pyinspect
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub

pytestmark = pytest.mark.unit


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(title, title, "Author", now, now, "1.0", now,
                    "book-%d" % book_id, 1, [], [])
    book.id = book_id
    return book


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def calibre_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([_book(1, "One"), _book(2, "Two"), _book(3, "Three")])
    session.commit()
    yield session
    session.close()


def _user(session, name, enabled):
    user = ub.User(name=name, email="%s@example.invalid" % name, password="",
                   has_own_library=enabled, default_language="all")
    session.add(user)
    session.commit()
    return user


def _cdb(calibre_session):
    instance = object.__new__(db.CalibreDB)
    instance.session = calibre_session
    instance.config = SimpleNamespace(config_restricted_column=0)
    return instance


def test_schema_and_role_contract(app_session):
    user = _user(app_session, "reader", True)
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=7, added_by=user.id))
    app_session.commit()
    row = app_session.query(ub.UserLibraryBook).one()
    assert row.added_at is not None
    assert constants.ROLE_BROWSE_GLOBAL == 1 << 9
    user.role |= constants.ROLE_BROWSE_GLOBAL
    assert user.role_browse_global()


def test_two_users_are_scoped_and_default_off_is_unchanged(
        app_session, calibre_session, monkeypatch):
    alice = _user(app_session, "alice", True)
    bob = _user(app_session, "bob", True)
    legacy = _user(app_session, "legacy", False)
    app_session.add_all([
        ub.UserLibraryBook(user_id=alice.id, book_id=1, added_by=alice.id),
        ub.UserLibraryBook(user_id=bob.id, book_id=2, added_by=bob.id),
    ])
    app_session.commit()
    monkeypatch.setattr(db.ub, "session", app_session)
    cdb = _cdb(calibre_session)

    monkeypatch.setattr(db, "current_user", alice)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [1]
    monkeypatch.setattr(db, "current_user", bob)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [2]
    monkeypatch.setattr(db, "current_user", legacy)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [1, 2, 3]


def test_policy_funnel_is_wired_to_web_opds_shelf_and_kobo():
    from cps import kobo, opds, shelf

    web_source = pyinspect.getsource(db.CalibreDB.fill_indexpage_with_archived_books)
    opds_source = pyinspect.getsource(opds.get_opds_restricted_common_filter)
    shelf_source = pyinspect.getsource(shelf.add_book_to_shelf)
    kobo_source = pyinspect.getsource(kobo.HandleSyncRequest)
    assert "self.common_filters(" in web_source
    assert "calibre_db.common_filters(" in opds_source
    assert "calibre_db.common_filters()" in shelf_source
    assert "calibre_db.common_filters(allow_show_archived=True)" in kobo_source


def test_membership_filter_uses_one_json_bind_not_an_expanding_integer_list(
        app_session, calibre_session, monkeypatch):
    user = _user(app_session, "large", True)
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=book_id, added_by=user.id)
        for book_id in range(1, 20001)
    ])
    app_session.commit()
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)
    expression = _cdb(calibre_session).common_filters()
    compiled = str(expression.compile())
    assert "json_each" in compiled
    assert "json_group_array" not in compiled  # aggregation stays in app.db
    assert compiled.count(":") < 20


def test_membership_filter_is_built_once_per_request(
        app_session, calibre_session, monkeypatch):
    user = _user(app_session, "cached", True)
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=book_id, added_by=user.id)
        for book_id in (1, 2, 3)
    ])
    app_session.commit()
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)
    statements = []
    event.listen(
        app_session.bind,
        "after_cursor_execute",
        lambda _conn, _cursor, statement, _params, _ctx, _many:
            statements.append(statement),
    )
    app = Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        cdb = _cdb(calibre_session)
        cdb.common_filters()
        cdb.common_filters()
    membership_reads = [
        statement for statement in statements
        if "json_group_array" in statement and "user_library_book" in statement
    ]
    assert len(membership_reads) == 1


def test_membership_rows_do_not_cascade_into_kobo_or_reading_data(app_session):
    user = _user(app_session, "preserved", True)
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=9, added_by=user.id),
        ub.KoboSyncedBooks(user_id=user.id, book_id=9, book_uuid="uuid-9"),
        ub.ReadBook(user_id=user.id, book_id=9, read_status=ub.ReadBook.STATUS_FINISHED),
    ])
    app_session.commit()
    app_session.query(ub.UserLibraryBook).filter_by(user_id=user.id, book_id=9).delete()
    app_session.commit()
    assert app_session.query(ub.KoboSyncedBooks).filter_by(user_id=user.id, book_id=9).one()
    assert app_session.query(ub.ReadBook).filter_by(user_id=user.id, book_id=9).one()


def test_add_remove_contract_is_idempotent_shelf_aware_and_role_gated(
        app_session, calibre_session, monkeypatch):
    from cps import user_library

    user = _user(app_session, "curator", True)
    user.role |= constants.ROLE_BROWSE_GLOBAL
    app_session.commit()
    monkeypatch.setattr(db.ub, "session", app_session)
    cdb = _cdb(calibre_session)

    assert user_library.add_book(
        user, 1, added_by=user.id, app_session=app_session, cdb=cdb
    )
    assert user_library.add_book(
        user, 1, added_by=user.id, app_session=app_session, cdb=cdb
    )
    assert user_library.membership_count(user.id, app_session) == 1

    shelves = [
        ub.Shelf(name="Alpha", user_id=user.id, is_public=0),
        ub.Shelf(name="Beta", user_id=user.id, is_public=0),
    ]
    app_session.add_all(shelves)
    app_session.commit()
    for index, shelf in enumerate(shelves, 1):
        link = ub.BookShelf(shelf=shelf.id, book_id=1, order=index)
        link.ub_shelf = shelf
        app_session.add(link)
    app_session.add(ub.KoboSyncedBooks(
        user_id=user.id, book_id=1, book_uuid="uuid-1"
    ))
    app_session.add(ub.ReadBook(
        user_id=user.id, book_id=1,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    ))
    app_session.commit()
    impact = user_library.removal_impact(user, 1, app_session=app_session)
    assert impact == {
        "affected_shelves": ["Alpha", "Beta"],
        "kobo_removal_on_next_sync": True,
        "reading_data_preserved": True,
    }
    assert user_library.remove_book(user, 1, app_session=app_session) == [
        "Alpha", "Beta"
    ]
    assert app_session.query(ub.BookShelf).filter_by(book_id=1).count() == 0
    assert app_session.query(ub.KoboSyncedBooks).filter_by(
        user_id=user.id, book_id=1
    ).one()
    assert app_session.query(ub.ReadBook).filter_by(
        user_id=user.id, book_id=1
    ).one().read_status == ub.ReadBook.STATUS_IN_PROGRESS

    user_library.add_book(
        user, 1, added_by=user.id, app_session=app_session, cdb=cdb
    )
    assert user_library.membership_count(user.id, app_session) == 1
    assert app_session.query(ub.KoboSyncedBooks).filter_by(
        user_id=user.id, book_id=1
    ).one()
    assert app_session.query(ub.ReadBook).filter_by(
        user_id=user.id, book_id=1
    ).one().read_status == ub.ReadBook.STATUS_IN_PROGRESS
    user_library.remove_book(user, 1, app_session=app_session)

    user.role &= ~constants.ROLE_BROWSE_GLOBAL
    app_session.commit()
    with pytest.raises(user_library.UserLibraryError, match="global-library"):
        user_library.add_book(
            user, 2, added_by=user.id, app_session=app_session, cdb=cdb
        )
    with pytest.raises(user_library.UserLibraryError, match="empty set"):
        user_library.set_enabled(
            user, True, app_session=app_session, cdb=cdb
        )


def test_http_route_contract_is_registered():
    from flask import Flask
    from cps.api import api_v1
    from cps.web import web

    app = Flask(__name__)
    app.register_blueprint(api_v1)
    app.register_blueprint(web)
    routes = {}
    for rule in app.url_map.iter_rules():
        routes.setdefault(rule.rule, set()).update(rule.methods)
    assert routes["/api/v1/library/global"] >= {"GET"}
    assert routes["/api/v1/books/<int:book_id>/my-library"] >= {
        "GET", "PUT", "DELETE"
    }
    assert routes["/global-library"] >= {"GET"}
    assert routes["/ajax/mylibrary/<int:book_id>/add"] >= {"POST"}
    assert routes["/ajax/mylibrary/<int:book_id>/removal-impact"] >= {"GET"}
    assert routes["/ajax/mylibrary/<int:book_id>/remove"] >= {"POST"}


def test_seed_on_enable_is_chunked_idempotent_and_preserves_next_kobo_sync(
        monkeypatch):
    """The enable transition must not turn already-synced books into removals."""
    from cps import kobo as kobo_module, kobo_sync_status, user_library

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
    session = sessionmaker(bind=engine)()
    now = datetime(2026, 8, 28, 12, 0, 0)
    books = [_book(book_id, "Book %d" % book_id) for book_id in range(1, 6)]
    for book in books:
        book.last_modified = now
        book.timestamp = now
        book.uuid = "uuid-%d" % book.id
        session.add(book)
        session.add(db.Data(book.id, "EPUB", 1, "book-%d" % book.id))
    user = ub.User(
        name="seeded", email="seeded@example.invalid", password="",
        has_own_library=False, default_language="all",
        role=constants.ROLE_USER | constants.ROLE_DOWNLOAD,
        kobo_only_shelves_sync=0,
    )
    session.add(user)
    session.commit()
    session.add_all([
        ub.KoboSyncedBooks(user_id=user.id, book_id=book.id,
                           book_uuid=book.uuid)
        for book in books
    ])
    session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.config = SimpleNamespace(config_restricted_column=0)
    cdb.reconnect_db = lambda *_args, **_kwargs: None
    monkeypatch.setattr(db.ub, "session", session)
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(ub, "session", session)

    commits = []
    event.listen(session, "after_commit", lambda _session: commits.append(True))
    inserted = user_library.seed_user_library(
        user, added_by=user.id, chunk_size=2, app_session=session, cdb=cdb
    )
    first_seed_commits = len(commits)
    assert inserted == 5
    assert first_seed_commits == 4  # read release + three bounded write chunks
    assert user_library.seed_user_library(
        user, added_by=user.id, chunk_size=2, app_session=session, cdb=cdb
    ) == 0
    assert user_library.membership_count(user.id, session) == 5
    user.has_own_library = True
    session.commit()

    monkeypatch.setattr(kobo_module, "calibre_db", cdb)
    monkeypatch.setattr(kobo_module, "current_user", user)
    monkeypatch.setattr(kobo_sync_status, "current_user", user)
    monkeypatch.setattr(ub, "session_commit", lambda *_a, **_kw: session.commit())
    monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo_module.config, "config_kobo_sync_magic_shelves", False,
                        raising=False)
    monkeypatch.setattr(kobo_module, "get_download_url_for_book",
                        lambda *_args: "/download")
    monkeypatch.setattr(kobo_module, "get_magic_shelf_book_ids_for_kobo",
                        lambda _user_id: (set(), True))
    monkeypatch.setattr(kobo_module, "get_magic_shelf_membership_added_at",
                        lambda _user_id: None)
    monkeypatch.setattr(kobo_module, "sync_shelves", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        kobo_module, "create_book_entitlement",
        lambda book, archived=False: {"Id": str(book.id), "IsRemoved": archived},
    )
    monkeypatch.setattr(kobo_module, "get_metadata", lambda book: {"Id": str(book.id)})

    app = Flask(__name__)
    app.wsgi_app = SimpleNamespace(is_proxied=True)
    try:
        with app.test_request_context("/v1/library/sync"):
            response = kobo_module.HandleSyncRequest.__wrapped__()
        archived = [
            item for item in response.get_json()
            if item.get("ChangedEntitlement", {})
            .get("BookEntitlement", {}).get("IsRemoved") is True
        ]
        assert archived == []
        assert session.query(ub.ArchivedBook).filter_by(user_id=user.id).count() == 0
        assert session.query(ub.KoboSyncedBooks).filter_by(user_id=user.id).count() == 5
    finally:
        session.close()
        engine.dispose()


def test_schema_rollback_is_idempotent_and_leaves_user_data_tables_intact():
    from cps import config_sql

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    config_sql._Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = _user(session, "rollback", False)
    user.role |= constants.ROLE_BROWSE_GLOBAL
    session.add(ub.ReadBook(user_id=user.id, book_id=3,
                            read_status=ub.ReadBook.STATUS_FINISHED))
    session.commit()
    session.close()

    ub.rollback_user_library_schema(engine)
    ub.rollback_user_library_schema(engine)
    schema = inspect(engine)
    assert "user_library_book" not in schema.get_table_names()
    assert "has_own_library" not in {
        column["name"] for column in schema.get_columns("user")
    }
    assert "config_new_users_have_own_library" not in {
        column["name"] for column in schema.get_columns("settings")
    }
    assert "book_read_link" in schema.get_table_names()
    with engine.connect() as connection:
        role = connection.exec_driver_sql(
            "SELECT role FROM user WHERE name = 'rollback'"
        ).scalar_one()
        assert role & constants.ROLE_BROWSE_GLOBAL == 0
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM book_read_link"
        ).scalar_one() == 1

    # The normal startup migration restores the schema and is repeatable.
    migrated_session = sessionmaker(bind=engine)()
    ub.add_missing_tables(engine, migrated_session)
    ub.migrate_user_table(engine, migrated_session)
    config_sql._migrate_table(migrated_session, config_sql._Settings)
    ub.add_missing_tables(engine, migrated_session)
    ub.migrate_user_table(engine, migrated_session)
    config_sql._migrate_table(migrated_session, config_sql._Settings)
    migrated_schema = inspect(engine)
    assert "user_library_book" in migrated_schema.get_table_names()
    assert "has_own_library" in {
        column["name"] for column in migrated_schema.get_columns("user")
    }
    assert "config_new_users_have_own_library" in {
        column["name"] for column in migrated_schema.get_columns("settings")
    }
    migrated_session.close()
    engine.dispose()
