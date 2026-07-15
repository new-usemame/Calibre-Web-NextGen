# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Wire-level regression for #627's detached KOReader progress.

The reporter's devices exchanged the position through ``KOSyncProgress``, but
the book page had no ``KOReader Progress`` row.  Their log distinguishes an
existing ``ReadBook`` (book 1031) from a new one (book 1029): only the new-row
branch created ``KoboReadingState.current_bookmark``, which is the progress
carrier read by both classic and SPA book-detail endpoints.

This test replays the plugin's discovery/auth request followed by its progress
PUT against the real Flask blueprint and real SQLAlchemy models.  The existing
read row deliberately has no Kobo state, matching the reporter's legacy row.
"""

import sys
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, ub
from cps.progress_syncing.models import AppBase, KOSyncProgress


def _kosync_module():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.mark.unit
def test_existing_read_row_gets_visible_progress_state_from_plugin_put(monkeypatch):
    """Auth -> PUT must create the book-detail progress carrier if missing."""
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        session.add(ub.ReadBook(
            user_id=3,
            book_id=1031,
            read_status=ub.ReadBook.STATUS_UNREAD,
        ))
        session.commit()

        user = SimpleNamespace(id=3, name="Pocketbook")
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
                {
                    **response,
                    "calibre_book_id": 1031,
                    "calibre_book_title": "Die Zitronenblüten von Amalfi",
                    "calibre_book_format": "EPUB",
                    "calibre_checksum_version": "filename",
                },
                1031,
                "EPUB",
                "Die Zitronenblüten von Amalfi",
                "filename",
            ),
        )

        app = Flask(__name__)
        app.register_blueprint(module.kosync)
        client = app.test_client()
        headers = {
            "Accept": "application/vnd.koreader.v1+json",
            "Authorization": "Basic reporter-plugin-credentials",
        }

        handshake = client.get("/kosync/users/auth", headers=headers)
        assert handshake.status_code == 200
        assert handshake.get_json() == {"authorized": "OK"}

        pushed = client.put(
            "/kosync/syncs/progress",
            headers=headers,
            json={
                "document": "filename-digest-for-book-1031",
                "progress": "cre://reporter-position",
                "percentage": 0.6886,
                "device": "PocketBook",
                "device_id": "tablet-device-id",
            },
        )
        assert pushed.status_code == 200
        assert pushed.get_json()["calibre_book_id"] == 1031

        # Device-to-device position storage already worked in the report.
        stored = session.query(KOSyncProgress).one()
        assert stored.document == "1031"
        assert stored.percentage == pytest.approx(68.86)

        # This is the independent book-detail/UI carrier that was missing.
        visible = session.query(ub.KoboReadingState).filter_by(
            user_id=3, book_id=1031
        ).one()
        assert visible.current_bookmark is not None
        assert visible.current_bookmark.progress_percent == pytest.approx(68.86)
    finally:
        session.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("has_bookmark", "has_statistics"),
    ((False, True), (True, False), (False, False)),
)
def test_existing_partial_visible_state_is_completed(
    monkeypatch, has_bookmark, has_statistics
):
    """Long-running installs may have only part of the Kobo state graph."""
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        book_read = ub.ReadBook(
            user_id=3,
            book_id=1031,
            read_status=ub.ReadBook.STATUS_UNREAD,
        )
        reading_state = ub.KoboReadingState(user_id=3, book_id=1031)
        if has_bookmark:
            reading_state.current_bookmark = ub.KoboBookmark(progress_percent=12.0)
        if has_statistics:
            reading_state.statistics = ub.KoboStatistics()
        book_read.kobo_reading_state = reading_state
        session.add(book_read)
        session.commit()
        original_state_id = reading_state.id

        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(module.config, "config_read_column", 0, raising=False)
        module.update_book_read_status(
            SimpleNamespace(id=3, name="Pocketbook"), 1031, 68.86
        )
        session.commit()

        states = session.query(ub.KoboReadingState).filter_by(
            user_id=3, book_id=1031
        ).all()
        assert len(states) == 1
        assert states[0].id == original_state_id
        assert states[0].current_bookmark is not None
        assert states[0].current_bookmark.progress_percent == pytest.approx(68.86)
        assert states[0].statistics is not None
    finally:
        session.close()
