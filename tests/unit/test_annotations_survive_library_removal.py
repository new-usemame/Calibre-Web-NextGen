# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Removal/re-add contract for reading data owned by the global archive."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from flask import Flask, g
from flask_babel import Babel
from sqlalchemy import create_engine
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


def _cdb(calibre_session):
    instance = object.__new__(db.CalibreDB)
    instance.session = calibre_session
    instance.config = SimpleNamespace(config_restricted_column=0)
    return instance


def _user(session, name, *, browse_global=False):
    user = ub.User(
        name=name,
        email="%s@example.invalid" % name,
        password="",
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add(user)
    # Commit before touching role: the column default is applied on INSERT, so
    # `user.role |= ...` on an unflushed instance ORs against None. This is the
    # same order test_my_library_backend_1939._mode_user uses.
    session.commit()
    if browse_global:
        user.role |= constants.ROLE_BROWSE_GLOBAL
        session.commit()
    return user


def _seed_reading_data(session, user, book_id):
    """Create every user-owned reading carrier named in the removal contract."""
    stamp = datetime(2026, 8, 30, 12, 34, 56)
    device = ub.Device(
        user_id=user.id,
        kind="kobo",
        display_name="Preservation Kobo",
        created_by="auto",
    )
    state = ub.KoboReadingState(
        user_id=user.id,
        book_id=book_id,
        last_modified=stamp,
        priority_timestamp=stamp,
    )
    state.current_bookmark = ub.KoboBookmark(
        created_at=stamp,
        last_modified=stamp,
        location_source="content",
        location_type="KoboSpan",
        location_value="chapter-1#point(/1/2:3)",
        progress_percent=41.5,
        content_source_progress_percent=42.5,
    )
    session.add_all([
        device,
        ub.Annotation(
            user_id=user.id,
            book_id=book_id,
            annotation_id="annotation-%d" % book_id,
            source="kobo",
            annotation_type="highlight",
            highlighted_text="globally retained highlight",
            highlight_color="yellow",
            note_text="globally retained note",
            cfi_range="epubcfi(/6/2!/4/2/1:0)",
            chapter_progress=0.415,
            hidden=False,
            created_at=stamp,
            client_modified_at=stamp,
            server_modified_at=stamp,
        ),
        ub.Bookmark(
            user_id=user.id,
            book_id=book_id,
            format="EPUB",
            bookmark_key="epubcfi(/6/2!/4/2/1:0)",
        ),
        ub.ReadBook(
            user_id=user.id,
            book_id=book_id,
            read_status=ub.ReadBook.STATUS_IN_PROGRESS,
            last_modified=stamp,
            last_time_started_reading=stamp,
            times_started_reading=3,
        ),
        state,
        ub.KoboSyncedBooks(
            user_id=user.id,
            book_id=book_id,
            book_uuid="book-uuid-%d" % book_id,
        ),
    ])
    session.flush()
    annotation = session.query(ub.Annotation).filter_by(
        user_id=user.id, book_id=book_id,
    ).one()
    annotation.origin_device_id = device.id
    annotation.assigned_device_id = device.id
    session.add(ub.KoboDeviceBookEntitlement(
        device_id=device.id,
        book_id=book_id,
        fingerprint="f" * 64,
        payload_schema_version=1,
        change_basis='{"book":"stable"}',
        updated_at=stamp,
    ))
    session.commit()
    return device, _reading_data_snapshot(session, user.id, book_id)


def _reading_data_snapshot(session, user_id, book_id):
    annotation = session.query(ub.Annotation).filter_by(
        user_id=user_id, book_id=book_id,
    ).one()
    bookmark = session.query(ub.Bookmark).filter_by(
        user_id=user_id, book_id=book_id,
    ).one()
    read = session.query(ub.ReadBook).filter_by(
        user_id=user_id, book_id=book_id,
    ).one()
    state = session.query(ub.KoboReadingState).filter_by(
        user_id=user_id, book_id=book_id,
    ).one()
    synced = session.query(ub.KoboSyncedBooks).filter_by(
        user_id=user_id, book_id=book_id,
    ).one()
    entitlement = session.query(ub.KoboDeviceBookEntitlement).filter_by(
        book_id=book_id,
    ).one()
    return {
        "annotation": (
            annotation.id, annotation.annotation_id, annotation.source,
            annotation.annotation_type, annotation.highlighted_text,
            annotation.highlight_color, annotation.note_text,
            annotation.cfi_range, annotation.chapter_progress,
            annotation.origin_device_id, annotation.assigned_device_id,
        ),
        "bookmark": (
            bookmark.id, bookmark.format, bookmark.bookmark_key,
        ),
        "read_book": (
            read.id, read.read_status, read.last_modified,
            read.last_time_started_reading, read.times_started_reading,
        ),
        "kobo_reading_state": (
            state.id, state.last_modified, state.priority_timestamp,
        ),
        "kobo_bookmark": (
            state.current_bookmark.id,
            state.current_bookmark.kobo_reading_state_id,
            state.current_bookmark.location_source,
            state.current_bookmark.location_type,
            state.current_bookmark.location_value,
            state.current_bookmark.progress_percent,
            state.current_bookmark.content_source_progress_percent,
        ),
        "kobo_synced_book": (
            synced.id, synced.book_uuid,
        ),
        "kobo_device_entitlement": (
            entitlement.id, entitlement.device_id, entitlement.fingerprint,
            entitlement.payload_schema_version, entitlement.change_basis,
        ),
    }


def _assert_reading_data_unchanged(session, user_id, book_id, expected):
    assert _reading_data_snapshot(session, user_id, book_id) == expected


def test_single_remove_preserves_every_reading_data_carrier(app_session):
    from cps import user_library

    user = _user(app_session, "single-remove")
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=1),
        ub.UserLibraryBook(user_id=user.id, book_id=2),
    ])
    app_session.commit()
    _device, expected = _seed_reading_data(app_session, user, 1)

    user_library.remove_book(user, 1, app_session=app_session)

    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=1,
    ).count() == 0
    _assert_reading_data_unchanged(app_session, user.id, 1, expected)


def test_batch_remove_api_preserves_every_reading_data_carrier(
        app_session, monkeypatch):
    from cps.api import actions

    user = _user(app_session, "batch-remove")
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=1),
        ub.UserLibraryBook(user_id=user.id, book_id=2),
    ])
    app_session.commit()
    _device, expected = _seed_reading_data(app_session, user, 1)
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(actions, "current_user", user)
    app = Flask(__name__)

    with app.test_request_context(
            "/api/v1/books/my-library/batch", method="POST",
            json={"operation": "remove", "book_ids": [1]}):
        payload = actions.batch_my_library_membership.__wrapped__().get_json()

    assert payload["succeeded_ids"] == [1]
    assert payload["results"][0]["reading_data_preserved"] is True
    _assert_reading_data_unchanged(app_session, user.id, 1, expected)


def test_removing_last_book_preserves_every_reading_data_carrier(app_session):
    from cps import user_library

    user = _user(app_session, "empty-library", browse_global=True)
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    _device, expected = _seed_reading_data(app_session, user, 1)

    user_library.remove_book(user, 1, app_session=app_session)

    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id,
    ).count() == 0
    _assert_reading_data_unchanged(app_session, user.id, 1, expected)


def test_remove_then_readd_restores_exact_reading_state(
        app_session, calibre_session, monkeypatch):
    from cps import user_library

    user = _user(app_session, "remove-readd", browse_global=True)
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    _device, expected = _seed_reading_data(app_session, user, 1)
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(db.ub, "session", app_session)

    user_library.remove_book(user, 1, app_session=app_session)
    user_library.add_book(user, 1, app_session=app_session, cdb=cdb)

    assert app_session.query(ub.UserLibraryBook).filter_by(
        user_id=user.id, book_id=1,
    ).one()
    _assert_reading_data_unchanged(app_session, user.id, 1, expected)


def test_removed_book_remains_readable_in_annotations_view_and_all_exports(
        app_session, calibre_session, monkeypatch):
    from cps import annotations, user_library

    user = _user(app_session, "annotation-archive")
    app_session.add_all([
        ub.UserLibraryBook(user_id=user.id, book_id=1),
        ub.UserLibraryBook(user_id=user.id, book_id=2),
    ])
    app_session.commit()
    _device, _expected = _seed_reading_data(app_session, user, 1)
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(annotations, "calibre_db", cdb)
    monkeypatch.setattr(annotations, "current_user", user)
    monkeypatch.setattr(
        annotations, "render_title_template",
        lambda _template, **context: context,
    )
    user_library.remove_book(user, 1, app_session=app_session)
    app = Flask(__name__)
    # annotations_view calls gettext, which resolves app.extensions['babel'],
    # and builds url_for links to its sibling export endpoints, which needs the
    # blueprint registered.
    Babel(app)
    app.register_blueprint(annotations.annotations_bp)

    with app.test_request_context("/annotations/1"):
        # These views resolve cps.cw_login's current_user proxy. _get_user()
        # returns g._login_user when it is set and only falls back to
        # current_app.login_manager otherwise (cps/cw_login/utils.py:392), so
        # seeding it is the supported way to run the view as this user without
        # a session cookie.
        g._login_user = user
        view = annotations.annotations_view.__wrapped__(1)
        markdown = annotations.annotations_export_markdown.__wrapped__(1)
        csv = annotations.annotations_export_csv.__wrapped__(1)
        exported_json = annotations.annotations_export_json.__wrapped__(1)

    assert [row.annotation_id for row in view["annotations"]] == ["annotation-1"]
    assert "globally retained note" in markdown.get_data(as_text=True)
    assert "globally retained note" in csv.get_data(as_text=True)
    payload = json.loads(exported_json.get_data(as_text=True))
    assert payload["annotation_count"] == 1
    assert payload["annotations"][0]["note_text"] == "globally retained note"


def test_empty_library_keeps_removed_book_in_annotation_device_views(
        app_session, calibre_session, monkeypatch):
    from cps import annotations, user_library

    user = _user(app_session, "empty-device-archive", browse_global=True)
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    app_session.commit()
    device, _expected = _seed_reading_data(app_session, user, 1)
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(annotations, "calibre_db", cdb)
    monkeypatch.setattr(annotations, "current_user", user)
    user_library.remove_book(user, 1, app_session=app_session)
    app = Flask(__name__)

    with app.test_request_context(
            "/api/annotations/devices/%s/annotations" % device.public_id):
        detail = annotations.annotation_device_annotations.__wrapped__(
            device.public_id,
        ).get_json()
        summary = annotations.annotation_device_summary.__wrapped__(
            device.public_id,
        ).get_json()
        device_list = annotations.list_annotation_devices(
            user_id=user.id, session=app_session,
        )

    assert [row["annotation_id"] for row in detail["annotations"]] == [
        "annotation-1",
    ]
    assert detail["annotations"][0]["book"] == {"id": 1, "title": "One"}
    assert summary["highlights"] == 1
    assert device_list[0]["highlights"] == 1
