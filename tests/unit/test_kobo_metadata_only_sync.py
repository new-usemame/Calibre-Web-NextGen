# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import datetime
import inspect
from types import SimpleNamespace

import pytest
from flask_babel import Babel

from cps import db, editbooks, helper, kobo, ub
from tests.unit.test_1925_kobo_sync_dedownload import (
    _add_reading_state,
    sync_harness,  # noqa: F401
)

pytestmark = pytest.mark.unit


@pytest.fixture
def metadata_sync(sync_harness, monkeypatch):
    monkeypatch.setattr(kobo.calibre_db, "book_metadata_edits_available", True, raising=False)
    monkeypatch.setattr(helper, "calibre_db", kobo.calibre_db)
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    sync_harness.book.authors.append(db.Authors("Author", "Author"))
    sync_harness.book.pubdate = datetime(2019, 1, 1)
    sync_harness.session.commit()
    initial = sync_harness.sync()
    assert any("NewEntitlement" in item for item in initial.get_json())
    return sync_harness, initial.headers["x-kobo-synctoken"]


@pytest.mark.parametrize("suppress_replays", [True, False])
def test_existing_book_gets_metadata_without_arming_redownload(metadata_sync, monkeypatch, suppress_replays):
    harness, cursor = metadata_sync
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", suppress_replays)
    book = harness.book
    book.title = "Retitled"
    helper.mark_book_modified(book, set_dirty=False, metadata_only=True)
    harness.session.commit()
    # The New UI can save several fields in separate library transactions.
    book.title = "Final title"
    helper.mark_book_modified(book, set_dirty=False, metadata_only=True)
    harness.session.commit()

    response = harness.sync(cursor, acknowledge=False)
    items = response.get_json()
    assert not any("NewEntitlement" in item or "ChangedEntitlement" in item for item in items)
    [metadata] = [item["ChangedProductMetadata"]["BookMetadata"]
                  for item in items if "ChangedProductMetadata" in item]
    assert metadata["Title"] == "Final title"
    assert metadata["RevisionId"] == book.uuid
    assert harness.session.query(ub.KoboDeviceBookDownload).count() == 0
    assert harness.sync(cursor, acknowledge=False).data == response.data
    following = harness.sync(response.headers["x-kobo-synctoken"])
    assert not any("ChangedProductMetadata" in item for item in following.get_json())


def test_file_change_followed_by_metadata_edit_still_delivers_entitlement(metadata_sync):
    harness, cursor = metadata_sync
    book = harness.book
    book.data[0].uncompressed_size += 10
    helper.mark_book_modified(book, set_dirty=False)
    harness.session.commit()
    book.title = "Replacement"
    helper.mark_book_modified(book, set_dirty=False, metadata_only=True)
    harness.session.commit()

    items = harness.sync(cursor).get_json()
    assert not any("ChangedProductMetadata" in item for item in items)
    [entitlement] = [item["ChangedEntitlement"] for item in items if "ChangedEntitlement" in item]
    assert entitlement["BookMetadata"]["Title"] == "Replacement"


@pytest.fixture
def classic_editor(metadata_sync, monkeypatch, tmp_path):
    """Keep the real editor, ORM transaction and sync handler together."""
    harness, cursor = metadata_sync
    cdb = harness.calibre_db
    cdb.get_filtered_book = lambda *_a, **_kw: harness.book
    cdb.set_metadata_dirty = lambda book_id: None
    monkeypatch.setattr(editbooks, "calibre_db", cdb)
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(
        name="editor", role_edit=lambda: True,
    ))
    monkeypatch.setattr(editbooks.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(editbooks.config, "config_use_google_drive", False)
    monkeypatch.setattr(editbooks.constants, "CWA_METADATA_CHANGE_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(editbooks, "_queue_duplicate_scan_after_change", lambda *_a: None)
    Babel(harness.app)
    harness.app.add_url_rule(
        "/book/<int:book_id>", endpoint="web.show_book", view_func=lambda book_id: str(book_id),
    )
    harness.app.add_url_rule(
        "/admin/book/<int:book_id>", view_func=editbooks.do_edit_book, methods=["POST"],
    )
    return harness, cursor


@pytest.mark.parametrize("publication_date", ["2020-04-03", ""])
def test_classic_publication_date_edit_reaches_kobo_without_redownload(classic_editor, publication_date):
    """A date-only save or clear must advance sync's selection clock."""
    harness, cursor = classic_editor
    response = harness.app.test_client().post(
        "/admin/book/{}".format(harness.book.id),
        data={"authors": "Author", "comments": "", "pubdate": publication_date, "detail_view": "1"},
    )
    assert response.status_code == 302
    items = harness.sync(cursor).get_json()
    assert not any("NewEntitlement" in item or "ChangedEntitlement" in item for item in items)
    [metadata] = [item["ChangedProductMetadata"]["BookMetadata"]
                  for item in items if "ChangedProductMetadata" in item]
    expected = datetime(2020, 4, 3) if publication_date else db.Books.DEFAULT_PUBDATE
    assert metadata["PublicationDate"] == kobo.convert_to_kobo_timestamp_string(expected)
    assert harness.session.query(ub.KoboDeviceBookDownload).count() == 0


def test_unchanged_classic_save_does_not_create_a_metadata_delivery(classic_editor):
    """Saving the same date must not make the book look newly edited."""
    harness, cursor = classic_editor
    previous = harness.book.last_modified
    response = harness.app.test_client().post(
        "/admin/book/{}".format(harness.book.id),
        data={"authors": "Author", "comments": "", "pubdate": "2019-01-01", "detail_view": "1"},
    )
    assert response.status_code == 302
    assert harness.book.last_modified == previous
    assert harness.session.query(db.BookMetadataEdit).count() == 0
    assert harness.sync(cursor).get_json() == []


def test_new_ui_title_and_author_save_reaches_kobo(classic_editor, monkeypatch):
    """Drive the API's per-field commits through the real shared editor."""
    from cps.api import edit as api_edit

    harness, cursor = classic_editor
    monkeypatch.setattr(api_edit, "calibre_db", harness.calibre_db)
    monkeypatch.setattr(api_edit, "current_user", SimpleNamespace(
        is_authenticated=True, is_anonymous=False, role_edit=lambda: True,
    ))
    # Authentication and directory I/O have their own suites. Keep both
    # metadata handlers and their ORM commits real at this protocol seam.
    monkeypatch.setattr(api_edit, "edit_book_param", inspect.unwrap(editbooks.edit_book_param))
    monkeypatch.setattr(helper, "update_dir_structure", lambda *_a, **_kw: False)
    harness.calibre_db.get_cc_columns = lambda *_a, **_kw: []
    harness.app.add_url_rule(
        "/api/v1/books/<int:book_id>/metadata", methods=["POST"],
        view_func=inspect.unwrap(api_edit.update_metadata),
    )
    response = harness.app.test_client().post(
        "/api/v1/books/{}/metadata".format(harness.book.id),
        json={"title": "New title", "authors": "New Author"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert "errors" not in response.get_json()
    items = harness.sync(cursor).get_json()
    assert not any("NewEntitlement" in item or "ChangedEntitlement" in item for item in items)
    [metadata] = [item["ChangedProductMetadata"]["BookMetadata"]
                  for item in items if "ChangedProductMetadata" in item]
    assert metadata["Title"] == "New title"
    assert metadata["Contributors"] == ["New Author"]
    assert metadata["ContributorRoles"] == [{"Name": "New Author", "Role": "Author"}]
    assert harness.session.query(ub.KoboDeviceBookDownload).count() == 0


@pytest.mark.parametrize("evidence_gap", ["missing", "unavailable", "uuid", "archive"])
def test_uncertain_metadata_history_keeps_full_delivery(metadata_sync, evidence_gap, monkeypatch):
    """Missing history, recycled IDs and archive changes must fail closed."""
    harness, cursor = metadata_sync
    harness.book.title = "Edited"
    helper.mark_book_modified(harness.book, set_dirty=False, metadata_only=True)
    harness.session.commit()
    edit = harness.session.query(db.BookMetadataEdit).one()
    if evidence_gap == "missing":
        harness.session.delete(edit)
    elif evidence_gap == "unavailable":
        monkeypatch.setattr(harness.calibre_db, "book_metadata_edits_available", False)
    elif evidence_gap == "uuid":
        edit.book_uuid = "another-book"
    else:
        harness.session.add(ub.ArchivedBook(
            user_id=harness.user.id, book_id=harness.book.id,
            is_archived=False, last_modified=harness.book.last_modified,
        ))
    harness.session.commit()
    items = harness.sync(cursor).get_json()
    assert not any("ChangedProductMetadata" in item for item in items)
    [entitlement] = [item["ChangedEntitlement"] for item in items if "ChangedEntitlement" in item]
    assert entitlement["BookMetadata"]["Title"] == "Edited"


def test_rolled_back_metadata_edit_does_not_survive_in_tracking_table(metadata_sync):
    """The edit proof and the book change must share a transaction."""
    harness, cursor = metadata_sync
    old_title = harness.book.title
    harness.book.title = "Uncommitted"
    helper.mark_book_modified(harness.book, set_dirty=False, metadata_only=True)
    harness.session.rollback()
    assert harness.book.title == old_title
    assert harness.session.query(db.BookMetadataEdit).count() == 0
    assert harness.sync(cursor).get_json() == []


def test_metadata_edit_keeps_reading_state_separate_and_is_not_new_for_known_device(metadata_sync, monkeypatch):
    """A second Kobo still needs its first entitlement; the first keeps its place."""
    harness, cursor = metadata_sync
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")
    harness.book.title = "Retitled"
    helper.mark_book_modified(harness.book, set_dirty=False, metadata_only=True)
    harness.session.commit()
    _add_reading_state(harness, harness.book.last_modified, progress=42.0)
    other = ub.Device(
        user_id=harness.user.id, kind="kobo", display_name="Second Kobo", active=True,
    )
    harness.session.add(other)
    harness.session.commit()
    other_items = harness.sync(internal_device_id=other.id, raw_device_id="b" * 64).get_json()
    assert any("NewEntitlement" in item for item in other_items)
    assert not any("ChangedProductMetadata" in item for item in other_items)
    items = harness.sync(cursor).get_json()
    assert any("ChangedProductMetadata" in item for item in items)
    assert not any("NewEntitlement" in item or "ChangedEntitlement" in item for item in items)
    [state] = [item["ChangedReadingState"]["ReadingState"]
               for item in items if "ChangedReadingState" in item]
    assert state["CurrentBookmark"]["ProgressPercent"] == 42.0
