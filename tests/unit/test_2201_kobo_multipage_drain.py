# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""A bulk-imported dual-format library must reach the reader across sessions.

Use HTTP dispatch and the returned opaque token for acknowledgment; do not
promote delivery ledgers from the test. SQLite timestamps are literal TEXT so
ORM serialization cannot erase the mixed-spelling page-boundary case.
"""

from datetime import datetime

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness
from tests.unit.test_kobo_replay_topup_pagination import _populate_library


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("shelf_only", [False, True], ids=["all", "shelf"])
@pytest.mark.parametrize("mixed_text", [False, True], ids=["tied", "mixed-text"])
def test_bulk_library_drains_across_sync_sessions(
    sync_harness, monkeypatch, shelf_only, mixed_text,
):
    from cps import db, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
        raising=False,
    )
    books = _populate_library(sync_harness, 296, tied=True)
    expected = {str(book.uuid) for book in books}
    session = sync_harness.session
    sync_harness.user.kobo_only_shelves_sync = shelf_only
    shelf = ub.Shelf(name="Bulk import", user_id=sync_harness.user.id,
                     kobo_sync=True)
    session.add(shelf)
    session.flush()
    for book in books:
        session.add(db.Data(book.id, "KEPUB", 3_000_000, book.path))
        session.add(ub.BookShelf(
            book_id=book.id, ub_shelf=shelf, order=book.id,
            date_added=datetime(2026, 2, 1),
        ))
    session.commit()
    spellings = (
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00.000000",
        "2026-01-01 00:00:00+00:00",
        "2026-01-01 00:00:00.000000+00:00",
    )
    for index, book in enumerate(books):
        session.connection().exec_driver_sql(
            "UPDATE books SET last_modified = ? WHERE id = ?",
            (spellings[index % len(spellings)] if mixed_text else spellings[1],
             book.id),
        )
    session.commit()
    session.expire_all()

    token = None
    delivered = []
    changed_before_new = []
    pages = []
    terminal = False
    client = sync_harness.app.test_client()
    # Each request is a successive sync session. Local pages intentionally
    # omit continuation so firmware persists their cursor (#1634). Once a
    # session delivers nothing, further sessions must stay quiet.
    for _ in range(8):
        headers = {"x-kobo-deviceid": "a" * 64,
                   "x-kobo-devicemodel": "Kobo Clara BW"}
        if token is not None:
            headers[sync_harness.token_header] = token
        response = client.get("/v1/library/sync", headers=headers)
        assert response.status_code == 200, response.get_data(as_text=True)
        token = response.headers[sync_harness.token_header]
        new = []
        changed = []
        for item in response.get_json():
            for kind in ("NewEntitlement", "ChangedEntitlement"):
                if kind not in item:
                    continue
                entitlement = item[kind]["BookEntitlement"]
                assert not entitlement["IsRemoved"]
                book_uuid = entitlement["Id"]
                if kind == "NewEntitlement":
                    new.append(book_uuid)
                else:
                    changed.append(book_uuid)
                    if book_uuid not in delivered:
                        changed_before_new.append(book_uuid)
        pages.append((len(new), len(changed), response.headers.get("x-kobo-sync")))
        assert len(new) + len(changed) <= kobo.SYNC_ITEM_LIMIT
        if terminal:
            assert not new and not changed, pages
        delivered.extend(new)
        if not new and not changed and response.headers.get("x-kobo-sync") != "continue":
            terminal = True

    assert set(delivered) == expected, (
        f"Only {len(set(delivered))}/296 books received NewEntitlement; "
        f"changed before new={len(changed_before_new)}; pages={pages}"
    )
    assert not changed_before_new, pages
    assert len(delivered) == len(expected), pages
    assert terminal, pages
    print(f"drain shelf_only={shelf_only} mixed_text={mixed_text}: {pages}")
