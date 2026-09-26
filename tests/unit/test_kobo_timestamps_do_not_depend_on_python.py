# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Kobo timestamps are the same bytes on every interpreter.

Calibre stores "no date" as year 101. The released image (Python 3.13, glibc)
has always sent that as "101-01-01T00:00:00Z"; strftime on macOS or Python
3.14 pads it to "0101-...". A payload that changes with the interpreter makes
every undated book look Changed to every Kobo after an upgrade.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from cps.kobo import convert_to_kobo_timestamp_string


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value, expected", [
    (datetime(101, 1, 1), "101-01-01T00:00:00Z"),
    (date(101, 1, 1), "101-01-01T00:00:00Z"),
    (datetime(2026, 9, 25, 7, 4, 3, 999999), "2026-09-25T07:04:03Z"),
    (datetime(2026, 9, 25, 7, 4, 3, tzinfo=timezone(timedelta(hours=-4))), "2026-09-25T07:04:03Z"),
    (datetime(999, 12, 31, 23, 59, 59), "999-12-31T23:59:59Z"),
    (None, "1970-01-01T00:00:00Z"),
])
def test_kobo_timestamp_bytes(value, expected):
    assert convert_to_kobo_timestamp_string(value) == expected


# A server that ran on macOS or Python 3.14 stored its ledger fingerprints from
# the padded bytes. Moving that library to the image must not re-send its
# undated books: a ChangedEntitlement on a held book re-downloads it.

from tests.unit.test_1925_kobo_sync_dedownload import (  # noqa: E402,F401 - fixture
    _entitlements,
    sync_harness,
)


def _seed_ledger_as_a_padding_server(sync_harness, monkeypatch, undated_added=False):
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    assert sync_harness.book.pubdate.year == 101
    if undated_added:
        # A second year-101 field: the twin must pad every one, not one key.
        sync_harness.book.timestamp = datetime(101, 1, 1)
        sync_harness.session.commit()
    unpadded = kobo.convert_to_kobo_timestamp_string

    def padded(timestamp):
        year, rest = unpadded(timestamp).split("-", 1)
        return "%04d-%s" % (int(year), rest)

    monkeypatch.setattr(kobo, "convert_to_kobo_timestamp_string", padded)
    assert len(_entitlements(sync_harness.sync())) == 1
    monkeypatch.setattr(kobo, "convert_to_kobo_timestamp_string", unpadded)
    return sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one().fingerprint


@pytest.mark.parametrize("undated_added", [False, True],
                         ids=["no-publication-date", "no-dates-at-all"])
def test_an_undated_book_is_not_resent_after_moving_off_a_padding_server(
    sync_harness, monkeypatch, undated_added,
):
    from cps import kobo, ub

    padded_fingerprint = _seed_ledger_as_a_padding_server(
        sync_harness, monkeypatch, undated_added,
    )
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()

    assert _entitlements(sync_harness.sync(stale_token)) == []

    sync_harness.session.expire_all()
    restamped = sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).one().fingerprint
    assert restamped != padded_fingerprint
    # The record now holds this server's own bytes: an exact replay is
    # suppressed without the padded equivalence.
    monkeypatch.setattr(kobo, "_entitlement_fingerprint_twin", lambda _e: None)
    assert _entitlements(sync_harness.sync(stale_token)) == []


def test_a_real_edit_is_still_delivered_over_a_padded_record(
    sync_harness, monkeypatch,
):
    from cps import kobo

    _seed_ledger_as_a_padding_server(sync_harness, monkeypatch)
    sync_harness.book.title = "Retitled"
    sync_harness.book.last_modified = datetime(2026, 9, 1, 9, 0, 0)
    sync_harness.session.commit()
    stale_token = kobo.SyncToken.SyncToken().build_sync_token()

    delivered = _entitlements(sync_harness.sync(stale_token))

    assert len(delivered) == 1
    assert delivered[0]["ChangedEntitlement"]["BookMetadata"]["Title"] == "Retitled"
