# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the Kobo archive/deletion cursor."""

from datetime import datetime, timedelta

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness


pytestmark = pytest.mark.unit


def _outgoing_token(sync_harness, response):
    from cps.services import SyncToken

    return SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            response.headers[sync_harness.token_header],
    })


def _removed_book_ids(response):
    return [
        item["ChangedEntitlement"]["BookEntitlement"]["Id"]
        for item in response.get_json()
        if item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
    ]


def test_empty_archive_pass_never_regresses_consumed_tombstone_cursor(
    sync_harness, monkeypatch,
):
    """Consumed tombstones cannot make archive_modified alternate with epoch."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    # Cross the migration boundary before these deletions exist so their first
    # delivery is real and later requests exercise acknowledged suppression.
    initial = sync_harness.sync()
    incoming_header = initial.headers[sync_harness.token_header]
    assert sync_harness.session.query(ub.ArchivedBook).count() == 0

    deleted_at = datetime(2026, 8, 28, 13, 0, 0)
    deleted_ids = {
        f"00000000-0000-0000-0004-{sequence:012d}"
        for sequence in range(5)
    }
    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=book_uuid,
            deleted_at=deleted_at + timedelta(seconds=sequence),
        )
        for sequence, book_uuid in enumerate(sorted(deleted_ids))
    ])
    sync_harness.session.commit()

    removed_by_request = []
    archive_cursors = []
    for _request in range(6):
        incoming = kobo.SyncToken.SyncToken.from_headers({
            sync_harness.token_header: incoming_header,
        })
        response = sync_harness.sync(incoming_header)
        outgoing = _outgoing_token(sync_harness, response)

        assert outgoing.archive_last_modified >= \
            incoming.archive_last_modified
        removed_by_request.append(set(_removed_book_ids(response)))
        archive_cursors.append(outgoing.archive_last_modified)
        incoming_header = response.headers[sync_harness.token_header]

    assert removed_by_request[0] == deleted_ids
    assert removed_by_request[1:] == [set()] * 5
    assert archive_cursors == [archive_cursors[0]] * 6


def test_newer_archive_change_does_not_mask_unseen_tombstone(
    sync_harness, monkeypatch,
):
    """Tombstone selection remains based on the reader's incoming cursor."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    initial = sync_harness.sync()
    incoming_header = initial.headers[sync_harness.token_header]
    incoming = _outgoing_token(sync_harness, initial)
    deleted_at = datetime(2026, 8, 28, 13, 0, 0)
    archived_at = deleted_at + timedelta(hours=1)
    deleted_id = "00000000-0000-0000-0004-000000000099"

    # Keep this live row inside the changed-entry snapshot. Its newer archive
    # clock must advance the response without becoming the tombstone query's
    # lower bound and masking the distinct deletion between the two cursors.
    sync_harness.book.last_modified = deleted_at - timedelta(minutes=30)

    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=deleted_id,
            deleted_at=deleted_at,
        ),
        ub.ArchivedBook(
            user_id=sync_harness.user.id,
            book_id=sync_harness.book.id,
            is_archived=True,
            last_modified=archived_at,
        ),
    ])
    sync_harness.session.commit()

    first = sync_harness.sync(incoming_header)
    first_outgoing = _outgoing_token(sync_harness, first)
    assert incoming.archive_last_modified < deleted_at < archived_at
    assert _removed_book_ids(first).count(deleted_id) == 1
    assert first_outgoing.archive_last_modified == archived_at

    second = sync_harness.sync(
        first.headers[sync_harness.token_header],
    )
    second_outgoing = _outgoing_token(sync_harness, second)
    assert deleted_id not in _removed_book_ids(second)
    assert second_outgoing.archive_last_modified == archived_at


def test_backdated_tombstone_missing_from_device_ledger_is_delivered(
    sync_harness, monkeypatch,
):
    """The device ledger recovers a deletion behind its timestamp cursor."""
    from cps import admin, kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    initial = sync_harness.sync()
    high_watermark = datetime(2026, 8, 28, 15, 0, 0)
    acknowledged_id = "00000000-0000-0000-0004-000000000200"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=acknowledged_id,
        deleted_at=high_watermark,
    ))
    sync_harness.session.commit()

    acknowledged = sync_harness.sync(
        initial.headers[sync_harness.token_header],
    )
    assert _removed_book_ids(acknowledged) == [acknowledged_id]
    assert _outgoing_token(
        sync_harness, acknowledged,
    ).archive_last_modified == high_watermark

    backdated_id = "00000000-0000-0000-0004-000000000201"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=backdated_id,
        deleted_at=high_watermark - timedelta(hours=1),
    ))
    sync_harness.session.commit()

    recovered = sync_harness.sync(
        acknowledged.headers[sync_harness.token_header],
    )
    assert _removed_book_ids(recovered) == [backdated_id]
    assert _outgoing_token(
        sync_harness, recovered,
    ).archive_last_modified == high_watermark

    following = sync_harness.sync(
        recovered.headers[sync_harness.token_header],
    )
    assert backdated_id not in _removed_book_ids(following)
    assert sync_harness.session.query(
        ub.KoboDeviceDeletedEntitlement,
    ).filter_by(
        device_id=sync_harness.device.id,
        book_uuid=backdated_id,
    ).count() == 1

    monkeypatch.setattr(admin, "_", lambda value: value)
    with sync_harness.app.test_request_context(
        "/ajax/fullsync/17", method="POST",
    ):
        reset = admin.do_full_kobo_sync(sync_harness.user.id)
    assert reset.status_code == 200

    deliberate_reannouncement = sync_harness.sync(
        following.headers[sync_harness.token_header],
    )
    assert set(_removed_book_ids(deliberate_reannouncement)) == {
        acknowledged_id,
        backdated_id,
    }


def test_missing_ledger_tombstone_reannouncement_respects_page_cap(
    sync_harness, monkeypatch,
):
    """A large recovery frontier delivers at most one bounded page at a time."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    initial = sync_harness.sync()
    high_watermark = datetime(2026, 8, 28, 16, 0, 0)
    anchor_id = "00000000-0000-0000-0004-000000000300"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=anchor_id,
        deleted_at=high_watermark,
    ))
    sync_harness.session.commit()
    anchor = sync_harness.sync(initial.headers[sync_harness.token_header])
    assert _removed_book_ids(anchor) == [anchor_id]

    backdated_ids = [
        f"00000000-0000-0000-0005-{sequence:012d}"
        for sequence in range(kobo.SYNC_ITEM_LIMIT + 1)
    ]
    backdated_base = high_watermark - timedelta(hours=2)
    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=book_uuid,
            deleted_at=backdated_base + timedelta(seconds=sequence),
        )
        for sequence, book_uuid in enumerate(backdated_ids)
    ])
    sync_harness.session.commit()

    first = sync_harness.sync(anchor.headers[sync_harness.token_header])
    first_removed = _removed_book_ids(first)
    assert len(first_removed) == kobo.SYNC_ITEM_LIMIT
    assert set(first_removed).issubset(backdated_ids)
    assert _outgoing_token(
        sync_harness, first,
    ).archive_last_modified == high_watermark

    second = sync_harness.sync(first.headers[sync_harness.token_header])
    second_removed = _removed_book_ids(second)
    assert len(second_removed) == 1
    assert set(first_removed + second_removed) == set(backdated_ids)
    assert _outgoing_token(
        sync_harness, second,
    ).archive_last_modified == high_watermark

    terminal = sync_harness.sync(
        second.headers[sync_harness.token_header],
    )
    assert _removed_book_ids(terminal) == []
