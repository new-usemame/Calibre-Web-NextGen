# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""F-6f9187 — KOSync must not regress the Kobo-visible bookmark.

The KOSync row and ``KoboBookmark`` are separate position carriers. A KOReader
push is first arbitrated against the KOSync row, then mirrored onto the bookmark
that the Kobo sync feed serves. The second write needs its own invocation of the
shared resolved-position arbiter: an accepted KOSync position is not necessarily
allowed to replace a further position learned from another carrier.

Pinned here:
  * a first low KOReader push cannot erase a pre-bridge Kobo position;
  * a same-device rewind remains accepted by KOSync but stays device-local;
  * genuinely further KOReader progress still reaches the Kobo carrier.
"""

import sys
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, ub
from cps.progress_syncing.models import AppBase, KOSyncProgress

USER_ID = 3
BOOK_ID = 1031
DEVICE_ID = "tablet-device-id"


def _kosync_module():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def progress_api(monkeypatch):
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    user = SimpleNamespace(id=USER_ID, name="PocketBook")
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: user)
    monkeypatch.setattr(module, "get_book_checksums", lambda _book_id: [])
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_args: None)
    monkeypatch.setattr(calibre_db, "get_book", lambda _book_id: SimpleNamespace())
    monkeypatch.setattr(module.config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(
        module,
        "enrich_response_with_book_info",
        lambda response, _document: (
            {**response, "calibre_book_id": BOOK_ID},
            BOOK_ID,
            "EPUB",
            "Fixture",
            "filename",
        ),
    )

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    yield SimpleNamespace(module=module, client=app.test_client(), session=session)

    session.close()
    engine.dispose()


def _seed_visible_progress(session, percentage):
    book_read = ub.ReadBook(
        user_id=USER_ID,
        book_id=BOOK_ID,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    state = ub.KoboReadingState(user_id=USER_ID, book_id=BOOK_ID)
    state.current_bookmark = ub.KoboBookmark(progress_percent=percentage)
    state.statistics = ub.KoboStatistics()
    book_read.kobo_reading_state = state
    session.add(book_read)
    session.commit()


def _push(progress_api, percentage, *, device_id=DEVICE_ID):
    response = progress_api.client.put(
        "/kosync/syncs/progress",
        json={
            "document": "filename-digest-for-book-1031",
            "progress": "cre://fixture-position",
            "percentage": percentage / 100.0,
            "device": "PocketBook",
            "device_id": device_id,
        },
    )
    assert response.status_code == 200
    progress_api.session.expire_all()


def _visible_percentage(session):
    state = session.query(ub.KoboReadingState).filter_by(
        user_id=USER_ID,
        book_id=BOOK_ID,
    ).one()
    return state.current_bookmark.progress_percent


@pytest.mark.unit
def test_first_low_koreader_push_does_not_regress_existing_kobo_bookmark(
    progress_api,
):
    """No KOSync row exists, but the Kobo carrier already holds 70%."""
    _seed_visible_progress(progress_api.session, 70.0)

    _push(progress_api, 5.0)

    assert progress_api.session.query(KOSyncProgress).one().percentage == pytest.approx(5.0)
    assert _visible_percentage(progress_api.session) == pytest.approx(70.0)


@pytest.mark.unit
def test_same_device_koreader_rewind_stays_local_to_kosync(progress_api):
    """The device owns its KOSync locator, not the cross-carrier frontier."""
    _seed_visible_progress(progress_api.session, 70.0)
    progress_api.session.add(KOSyncProgress(
        user_id=USER_ID,
        document=str(BOOK_ID),
        progress="cre://further-position",
        percentage=70.0,
        device="PocketBook",
        device_id=DEVICE_ID,
    ))
    progress_api.session.commit()

    _push(progress_api, 20.0)

    stored = progress_api.session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(20.0), "same-device rewind remains accepted"
    assert _visible_percentage(progress_api.session) == pytest.approx(70.0)


@pytest.mark.unit
def test_further_koreader_push_still_advances_kobo_bookmark(progress_api):
    _seed_visible_progress(progress_api.session, 70.0)

    _push(progress_api, 85.0)

    assert progress_api.session.query(KOSyncProgress).one().percentage == pytest.approx(85.0)
    assert _visible_percentage(progress_api.session) == pytest.approx(85.0)
