# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cps import helper, kobo, ub
from tests.unit.test_1925_kobo_sync_dedownload import sync_harness  # noqa: F401

pytestmark = pytest.mark.unit


@pytest.fixture
def metadata_sync(sync_harness, monkeypatch):
    monkeypatch.setattr(kobo.calibre_db, "kobo_metadata_edits_available", True, raising=False)
    monkeypatch.setattr(helper, "calibre_db", kobo.calibre_db)
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    initial = sync_harness.sync()
    assert any("NewEntitlement" in item for item in initial.get_json())
    return sync_harness, initial.headers["x-kobo-synctoken"]


def test_existing_book_gets_metadata_without_arming_redownload(metadata_sync):
    harness, cursor = metadata_sync
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
