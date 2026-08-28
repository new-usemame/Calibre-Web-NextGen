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
                   has_own_library=enabled, user_library_seeded=enabled,
                   default_language="all")
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
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=7))
    app_session.commit()
    row = app_session.query(ub.UserLibraryBook).one()
    assert row.added_at is not None
    assert constants.ROLE_BROWSE_GLOBAL == 1 << 9
    user.role |= constants.ROLE_BROWSE_GLOBAL
    assert user.role_browse_global()


def test_migration_adds_named_new_user_mode_setting():
    from cps import config_sql

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    config_sql._Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE settings DROP COLUMN "
            "config_new_users_personal_library"
        )
    ub.migrate_config_table(engine, session)
    columns = {
        column["name"] for column in inspect(engine).get_columns("settings")
    }
    assert "config_new_users_personal_library" in columns
    session.close()
    engine.dispose()


def test_two_users_are_scoped_and_default_off_is_unchanged(
        app_session, calibre_session, monkeypatch):
    first_user = _user(app_session, "first-user", True)
    second_user = _user(app_session, "second-user", True)
    legacy = _user(app_session, "legacy", False)
    app_session.add_all([
        ub.UserLibraryBook(user_id=first_user.id, book_id=1),
        ub.UserLibraryBook(user_id=second_user.id, book_id=2),
    ])
    app_session.commit()
    monkeypatch.setattr(db.ub, "session", app_session)
    cdb = _cdb(calibre_session)

    monkeypatch.setattr(db, "current_user", first_user)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [1]
    monkeypatch.setattr(db, "current_user", second_user)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [2]
    monkeypatch.setattr(db, "current_user", legacy)
    assert [b.id for b in calibre_session.query(db.Books)
            .filter(cdb.common_filters()).order_by(db.Books.id)] == [1, 2, 3]


def test_anonymous_browse_guest_uses_its_membership_set(
        app_session, calibre_session, monkeypatch):
    guest = _user(app_session, "Guest", True)
    guest.role = constants.ROLE_ANONYMOUS
    app_session.add(ub.UserLibraryBook(user_id=guest.id, book_id=2))
    app_session.commit()
    assert guest.is_anonymous
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", guest)
    app = Flask(__name__)
    with app.test_request_context("/?config_anonbrowse=1"):
        visible = (calibre_session.query(db.Books)
                   .filter(_cdb(calibre_session).common_filters())
                   .order_by(db.Books.id).all())
    assert [book.id for book in visible] == [2]


def _mode_round_trip(user, session):
    from cps import user_library

    assert user_library.set_library_mode(
        user, constants.LIBRARY_MODE_MONOLIBRARY, app_session=session
    ) == constants.LIBRARY_MODE_MONOLIBRARY
    assert user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL, app_session=session
    ) == constants.LIBRARY_MODE_PERSONAL


def _mode_user(session, name):
    user = _user(session, name, True)
    user.role |= constants.ROLE_BROWSE_GLOBAL
    session.commit()
    return user


def test_mode_round_trip_preserves_membership_including_curated_zero(app_session):
    curated = _mode_user(app_session, "curated")
    empty = _mode_user(app_session, "empty")
    app_session.add_all([
        ub.UserLibraryBook(user_id=curated.id, book_id=11),
        ub.UserLibraryBook(user_id=curated.id, book_id=12),
    ])
    app_session.commit()
    _mode_round_trip(curated, app_session)
    _mode_round_trip(empty, app_session)
    assert [row.book_id for row in app_session.query(ub.UserLibraryBook)
            .filter_by(user_id=curated.id).order_by(ub.UserLibraryBook.book_id)] == [11, 12]
    assert app_session.query(ub.UserLibraryBook).filter_by(user_id=empty.id).count() == 0
    assert curated.user_library_seeded is True
    assert empty.user_library_seeded is True


def test_mode_round_trip_preserves_shelves(app_session):
    user = _mode_user(app_session, "shelves")
    shelf = ub.Shelf(name="Keep", user_id=user.id, is_public=0)
    app_session.add(shelf)
    app_session.commit()
    link = ub.BookShelf(shelf=shelf.id, book_id=21, order=3)
    link.ub_shelf = shelf
    app_session.add(link)
    app_session.commit()
    before = [(row.shelf, row.book_id, row.order)
              for row in app_session.query(ub.BookShelf).all()]
    _mode_round_trip(user, app_session)
    assert [(row.shelf, row.book_id, row.order)
            for row in app_session.query(ub.BookShelf).all()] == before


def test_mode_round_trip_preserves_kobo_synced_books(app_session):
    user = _mode_user(app_session, "kobo-ledger")
    app_session.add(ub.KoboSyncedBooks(
        user_id=user.id, book_id=22, book_uuid="uuid-22"
    ))
    app_session.commit()
    _mode_round_trip(user, app_session)
    row = app_session.query(ub.KoboSyncedBooks).filter_by(user_id=user.id).one()
    assert (row.book_id, row.book_uuid) == (22, "uuid-22")


def test_mode_round_trip_preserves_reading_state(app_session):
    user = _mode_user(app_session, "reading")
    app_session.add_all([
        ub.ReadBook(user_id=user.id, book_id=23,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ub.KoboReadingState(user_id=user.id, book_id=23),
    ])
    app_session.commit()
    _mode_round_trip(user, app_session)
    assert app_session.query(ub.ReadBook).filter_by(
        user_id=user.id, book_id=23
    ).one().read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert app_session.query(ub.KoboReadingState).filter_by(
        user_id=user.id, book_id=23
    ).one()


def test_mode_round_trip_preserves_annotations(app_session):
    user = _mode_user(app_session, "annotations")
    app_session.add(ub.Annotation(
        user_id=user.id, book_id=24, annotation_id="annotation-24",
        highlighted_text="kept", note_text="also kept",
    ))
    app_session.commit()
    _mode_round_trip(user, app_session)
    row = app_session.query(ub.Annotation).filter_by(user_id=user.id).one()
    assert (row.book_id, row.highlighted_text, row.note_text) == (
        24, "kept", "also kept"
    )


def test_mode_round_trip_preserves_hidden_and_archived(app_session):
    user = _mode_user(app_session, "visibility")
    app_session.add_all([
        ub.UserHiddenBook(user_id=user.id, book_id=25),
        ub.ArchivedBook(user_id=user.id, book_id=26, is_archived=True),
    ])
    app_session.commit()
    _mode_round_trip(user, app_session)
    assert app_session.query(ub.UserHiddenBook).filter_by(
        user_id=user.id, book_id=25
    ).one()
    assert app_session.query(ub.ArchivedBook).filter_by(
        user_id=user.id, book_id=26
    ).one().is_archived is True


def test_mode_round_trip_preserves_sync_settings(app_session):
    user = _mode_user(app_session, "sync-settings")
    user.kobo_only_shelves_sync = 1
    user.opds_only_shelves_sync = 1
    user.kobo_two_way_annotation_sync = True
    user.kobo_two_way_annotation_scope = "selected"
    user.hardcover_token = "opaque-test-token"
    app_session.commit()
    before = (user.kobo_only_shelves_sync, user.opds_only_shelves_sync,
              user.kobo_two_way_annotation_sync,
              user.kobo_two_way_annotation_scope, user.hardcover_token)
    _mode_round_trip(user, app_session)
    assert (user.kobo_only_shelves_sync, user.opds_only_shelves_sync,
            user.kobo_two_way_annotation_sync,
            user.kobo_two_way_annotation_scope, user.hardcover_token) == before


def test_mode_round_trip_preserves_roles(app_session):
    user = _mode_user(app_session, "roles")
    user.role |= constants.ROLE_DOWNLOAD | constants.ROLE_EDIT_SHELFS
    app_session.commit()
    before = user.role
    _mode_round_trip(user, app_session)
    assert user.role == before


def test_intro_dismissal_is_durable_and_serialized(app_session):
    from cps import user_library
    from cps.api.serializers import serialize_user

    user = _mode_user(app_session, "intro")
    assert serialize_user(user)["show_my_library_intro"] is True
    user_library.dismiss_intro(user, app_session=app_session)
    app_session.expire_all()
    reloaded = app_session.query(ub.User).filter_by(id=user.id).one()
    payload = serialize_user(reloaded)
    assert payload["show_my_library_intro"] is False
    assert payload["library_mode"] == constants.LIBRARY_MODE_PERSONAL


def test_self_service_mode_and_intro_api_mutate_only_current_user(
        app_session, monkeypatch):
    from cps.api import account

    user = _mode_user(app_session, "self-service")
    other = _mode_user(app_session, "untouched-user")
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(account, "current_user", user)
    app = Flask(__name__)
    with app.test_request_context(
            "/api/v1/account/library-mode", method="POST",
            json={"mode": constants.LIBRARY_MODE_MONOLIBRARY}):
        response = account.update_library_mode()
        assert response.get_json()["library_mode"] == "monolibrary"
    with app.test_request_context(
            "/api/v1/account/library-mode", method="POST",
            json={"mode": constants.LIBRARY_MODE_PERSONAL}):
        response = account.update_library_mode()
        assert response.get_json()["library_mode"] == "personal_library"
    with app.test_request_context(
            "/api/v1/account/my-library-intro/dismiss", method="POST"):
        response = account.dismiss_my_library_intro()
        assert response.get_json()["show_my_library_intro"] is False
    app_session.refresh(other)
    assert other.library_mode() == constants.LIBRARY_MODE_PERSONAL
    assert other.my_library_intro_dismissed is False


def test_admin_api_switches_named_mode_for_target_user(app_session, monkeypatch):
    from cps.api import admin as api_admin

    administrator = ub.User(
        name="mode-admin", email="mode-admin@example.invalid", password="",
        role=constants.ROLE_ADMIN, default_language="all",
    )
    target = _mode_user(app_session, "mode-target")
    app_session.add(administrator)
    app_session.commit()
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(api_admin, "current_user", administrator)
    app = Flask(__name__)
    with app.test_request_context(
            "/api/v1/admin/users/%d" % target.id, method="POST",
            json={"library_mode": constants.LIBRARY_MODE_MONOLIBRARY}):
        response = api_admin.admin_update_user.__wrapped__(target.id)
        assert response.get_json()["library_mode"] == "monolibrary"
    with app.test_request_context(
            "/api/v1/admin/users/%d" % target.id, method="POST",
            json={"library_mode": constants.LIBRARY_MODE_PERSONAL}):
        response = api_admin.admin_update_user.__wrapped__(target.id)
        assert response.get_json()["library_mode"] == "personal_library"


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
        ub.UserLibraryBook(user_id=user.id, book_id=book_id)
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
        ub.UserLibraryBook(user_id=user.id, book_id=book_id)
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
        ub.UserLibraryBook(user_id=user.id, book_id=9),
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
        user, 1, app_session=app_session, cdb=cdb
    )
    assert user_library.add_book(
        user, 1, app_session=app_session, cdb=cdb
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

    user_library.add_book(user, 1, app_session=app_session, cdb=cdb)
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
        user_library.add_book(user, 2, app_session=app_session, cdb=cdb)
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
    assert routes["/api/v1/account/library-mode"] >= {"POST"}
    assert routes["/api/v1/account/my-library-intro/dismiss"] >= {"POST"}
    assert routes["/api/v1/admin/users/<int:user_id>"] >= {"POST"}
    assert routes["/global-library"] >= {"GET"}
    assert routes["/me"] >= {"GET", "POST"}
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
        has_own_library=False, user_library_seeded=False,
        default_language="all",
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
    assert user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=session, cdb=cdb, chunk_size=2,
    ) == constants.LIBRARY_MODE_PERSONAL
    first_enable_commits = len(commits)
    # Read release + three bounded write chunks + durable seed fence + mode.
    assert first_enable_commits == 6
    assert user.user_library_seeded is True
    assert user_library.seed_user_library(
        user, chunk_size=2, app_session=session, cdb=cdb
    ) == 0
    assert user_library.membership_count(user.id, session) == 5
    assert user_library.set_library_mode(
        user, constants.LIBRARY_MODE_MONOLIBRARY, app_session=session
    ) == constants.LIBRARY_MODE_MONOLIBRARY
    assert user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL, app_session=session
    ) == constants.LIBRARY_MODE_PERSONAL
    assert user_library.membership_count(user.id, session) == 5

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


def test_user_and_global_book_delete_cleanup_membership_rows(app_session, monkeypatch):
    from cps import admin, user_book_data

    keeper_admin = ub.User(
        name="admin", email="admin@example.invalid", password="",
        role=constants.ROLE_ADMIN, default_language="all",
    )
    target = _mode_user(app_session, "delete-me")
    other = _mode_user(app_session, "other")
    app_session.add(keeper_admin)
    app_session.commit()
    app_session.add_all([
        ub.UserLibraryBook(user_id=target.id, book_id=31),
        ub.UserLibraryBook(user_id=other.id, book_id=31),
        ub.UserLibraryBook(user_id=other.id, book_id=32),
    ])
    app_session.commit()

    user_book_data.purge_user_book_data(book_id=31, session=app_session)
    app_session.commit()
    assert app_session.query(ub.UserLibraryBook).filter_by(book_id=31).count() == 0
    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=other.id, book_id=32
    ).one()

    app_session.add(ub.UserLibraryBook(user_id=target.id, book_id=33))
    app_session.commit()
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(ub, "session_commit", lambda *_a, **_kw: app_session.commit())
    admin._delete_user(target)
    assert app_session.query(ub.UserLibraryBook).filter_by(user_id=target.id).count() == 0
    assert app_session.query(ub.User).filter_by(id=target.id).first() is None


def test_public_shelf_is_viewers_membership_intersection(
        app_session, calibre_session, monkeypatch):
    owner = _mode_user(app_session, "shelf-owner")
    viewer = _mode_user(app_session, "shelf-viewer")
    shelf = ub.Shelf(name="Public", user_id=owner.id, is_public=1)
    app_session.add(shelf)
    app_session.commit()
    first = ub.BookShelf(shelf=shelf.id, book_id=1, order=1)
    second = ub.BookShelf(shelf=shelf.id, book_id=2, order=2)
    first.ub_shelf = shelf
    second.ub_shelf = shelf
    app_session.add_all([
        first, second, ub.UserLibraryBook(user_id=viewer.id, book_id=2),
    ])
    app_session.commit()
    shelf_book_ids = [row[0] for row in app_session.query(ub.BookShelf.book_id)
                      .filter_by(shelf=shelf.id).all()]
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", viewer)
    visible = (calibre_session.query(db.Books)
               .filter(db.Books.id.in_(shelf_book_ids))
               .filter(_cdb(calibre_session).common_filters())
               .order_by(db.Books.id).all())
    assert [book.id for book in visible] == [2]


def test_all_user_facet_counts_are_membership_scoped(
        app_session, calibre_session, monkeypatch):
    from cps.api import browse

    shared_author = db.Authors("Shared Author", "Shared Author")
    shared_series = db.Series("Shared Series", "Shared Series")
    shared_tag = db.Tags("Shared Tag")
    shared_publisher = db.Publishers("Shared Publisher", "Shared Publisher")
    shared_language = db.Languages("eng")
    books = calibre_session.query(db.Books).order_by(db.Books.id).all()
    for book in books[:2]:
        book.authors.append(shared_author)
        book.series.append(shared_series)
        book.tags.append(shared_tag)
        book.publishers.append(shared_publisher)
        book.languages.append(shared_language)
    calibre_session.commit()

    user = _mode_user(app_session, "facets")
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(db, "get_locale", lambda: "en")
    monkeypatch.setattr(browse, "calibre_db", cdb)
    calibre_session.connection().connection.driver_connection.create_function(
        "ng_sort_key", 1, lambda value: (value or "").lower()
    )
    app = Flask(__name__)
    with app.test_request_context("/api/v1/facets"):
        payloads = [
            browse.list_authors.__wrapped__(),
            browse.list_series.__wrapped__(),
            browse.list_tags.__wrapped__(),
            browse.list_publishers.__wrapped__(),
            browse.list_languages.__wrapped__(),
        ]
    for payload in payloads:
        assert len(payload["items"]) == 1
        assert payload["items"][0]["count"] == 1


def test_about_entity_counts_are_membership_scoped(
        app_session, calibre_session, monkeypatch):
    from cps.api import info

    author_one = db.Authors("One Author", "One Author")
    author_two = db.Authors("Two Author", "Two Author")
    tag_one, tag_two = db.Tags("One Tag"), db.Tags("Two Tag")
    series_one = db.Series("One Series", "One Series")
    series_two = db.Series("Two Series", "Two Series")
    books = calibre_session.query(db.Books).order_by(db.Books.id).all()
    books[0].authors.append(author_one)
    books[0].tags.append(tag_one)
    books[0].series.append(series_one)
    books[1].authors.append(author_two)
    books[1].tags.append(tag_two)
    books[1].series.append(series_two)
    calibre_session.commit()
    user = _mode_user(app_session, "about-counts")
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(db.ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(info, "calibre_db", cdb)
    monkeypatch.setattr(info, "current_user", user)
    app = Flask(__name__)
    with app.test_request_context("/api/v1/about"):
        response = info.about_info.__wrapped__()
        assert response.get_json()["counts"] == {
            "books": 1, "authors": 1, "categories": 1, "series": 1,
        }


def test_user_specific_catalog_responses_are_private_and_vary(monkeypatch):
    from flask import Response, g
    import cps

    monkeypatch.setattr(
        cps.config, "config_allow_reverse_proxy_header_login", True, raising=False
    )
    monkeypatch.setattr(
        cps.config, "config_reverse_proxy_login_header_name", "X-Remote-User",
        raising=False,
    )
    with cps.app.test_request_context("/api/v1/books"):
        g._common_filters_user_specific = True
        response = cps.protect_user_specific_catalog_responses(Response("ok"))
    assert response.headers["Cache-Control"] == "private, no-store"
    vary = {value.strip() for value in response.headers["Vary"].split(",")}
    assert vary == {"Cookie", "Authorization", "X-Remote-User"}


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
    rolled_back_user_columns = {
        column["name"] for column in schema.get_columns("user")
    }
    assert {
        "has_own_library", "user_library_seeded",
        "my_library_intro_dismissed",
    }.isdisjoint(rolled_back_user_columns)
    assert "config_new_users_personal_library" not in {
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
    migrated_user_columns = {
        column["name"] for column in migrated_schema.get_columns("user")
    }
    assert {
        "has_own_library", "user_library_seeded",
        "my_library_intro_dismissed",
    } <= migrated_user_columns
    assert "config_new_users_personal_library" in {
        column["name"] for column in migrated_schema.get_columns("settings")
    }
    migrated_session.close()
    engine.dispose()
