# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A held book is not re-sent because the library moved under it.

The Kobo cover id carries cover.jpg's file time, and the download URL carries
the address the device reached the server by. Copying a library without its
file times, or moving the server to a new domain, changed both for every book
without changing any book. The next time those books were sync candidates (a
stale token after a USB eject, a shelf edit) each was sent as Changed, and a
ChangedEntitlement on a held book makes the Kobo download it again.

A real edit, a new cover included, still reaches the device.
"""

import os
from datetime import datetime

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import (  # noqa: F401 - fixture
    _entitlements,
    sync_harness,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def library(sync_harness, monkeypatch, tmp_path):
    """A book with a cover on disk; ``held()`` syncs and acknowledges it."""
    from cps import kobo

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(tmp_path))
    # The shipped default; the shared harness turns it off.
    monkeypatch.setattr(kobo.config, "config_kobo_suppress_replayed_entitlements", True)
    book_dir = tmp_path / sync_harness.book.path
    book_dir.mkdir()
    cover = book_dir / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0 cover")
    os.utime(cover, (1_756_000_000, 1_756_000_000))
    sync_harness.cover = cover

    def held():
        first = _entitlements(sync_harness.sync())
        assert len(first) == 1
        sync_harness.first_cover_id = _cover_id(first[0])

    sync_harness.held = held
    return sync_harness


def _cover_id(entitlement):
    body = entitlement.get("NewEntitlement") or entitlement["ChangedEntitlement"]
    return body["BookMetadata"]["CoverImageId"]


def _stale_token():
    from cps import kobo

    return kobo.SyncToken.SyncToken().build_sync_token()


def _copy_without_file_times(library):
    os.utime(library.cover, (1_790_000_000, 1_790_000_000))


def _move_to_a_new_domain(library, monkeypatch):
    from cps import kobo

    monkeypatch.setattr(
        kobo, "get_download_url_for_book",
        lambda book_id, fmt: f"https://books.example.net/kobo/tok/download/{book_id}/{fmt}",
    )


@pytest.mark.parametrize("move", ["copied-without-file-times", "new-domain"])
def test_a_held_book_is_not_resent_after_the_library_moves(library, monkeypatch, move):
    library.held()
    if move == "copied-without-file-times":
        _copy_without_file_times(library)
    else:
        _move_to_a_new_domain(library, monkeypatch)

    assert _entitlements(library.sync(_stale_token())) == []


def _with_cover_id_and_url(value):
    """The projection every ledger row before schema 3 was hashed with."""
    if isinstance(value, dict):
        return {
            key: ([{k: v for k, v in entry.items() if k != "Size"} for entry in member]
                  if key == "DownloadUrls" else _with_cover_id_and_url(member))
            for key, member in value.items()
        }
    if isinstance(value, list):
        return [_with_cover_id_and_url(item) for item in value]
    return value


def test_a_ledger_written_before_this_release_stays_silent_across_a_move(
    library, monkeypatch,
):
    """Rows hashed with the cover id re-stamp instead of re-sending."""
    from cps import kobo, ub

    current = kobo._fingerprint_projection, kobo.ENTITLEMENT_PAYLOAD_SCHEMA_VERSION
    monkeypatch.setattr(kobo, "_fingerprint_projection", _with_cover_id_and_url)
    monkeypatch.setattr(kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", 2)
    library.held()
    monkeypatch.setattr(kobo, "_fingerprint_projection", current[0])
    monkeypatch.setattr(kobo, "ENTITLEMENT_PAYLOAD_SCHEMA_VERSION", current[1])
    _copy_without_file_times(library)
    _move_to_a_new_domain(library, monkeypatch)

    assert _entitlements(library.sync(_stale_token())) == []
    library.session.expire_all()
    assert library.session.query(ub.KoboDeviceBookEntitlement).one() \
        .payload_schema_version == current[1]


def test_a_new_cover_still_reaches_the_device(library):
    """Changing a cover through the library advances the book, and is sent."""
    library.held()
    _copy_without_file_times(library)
    library.book.last_modified = datetime(2026, 9, 20, 9, 0, 0)
    library.session.commit()

    delivered = _entitlements(library.sync(_stale_token()))

    assert len(delivered) == 1
    assert _cover_id(delivered[0]) != library.first_cover_id
