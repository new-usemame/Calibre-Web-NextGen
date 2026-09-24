# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade actual v4.1.43 handler output, not current rows with a reset flag."""
import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness, _entitlements
from cps import kobo, ub

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[1] / 'fixtures/kobo_upgrade/v4_1_43'


def _restore_tagged_database(h, scenario="emitted", *, downloaded=()):
    """Restore a v4.1.43 database; ``downloaded`` book ids were fetched.

    The generator drove only the sync handler.  A reader stores a book by
    downloading it, which records a ``Downloads`` row for the account, so a
    book the scenario says a reader holds is listed in ``downloaded``.
    """
    fixture = FIXTURE / scenario
    h.session.rollback()
    raw = h.session.connection().connection.driver_connection
    source = sqlite3.connect(':memory:')
    source.deserialize(gzip.decompress((fixture / 'main.db.gz').read_bytes()))
    assert 'classification_version' not in {row[1] for row in source.execute('PRAGMA table_info(kobo_device_entitlement_seed)')}
    assert 'payload_schema_version' not in {row[1] for row in source.execute('PRAGMA table_info(kobo_device_book_entitlement)')}
    source.backup(raw)
    source.close()
    with sqlite3.connect(':memory:') as formats:
        formats.deserialize(gzip.decompress((fixture / 'calibre.db.gz').read_bytes()))
        raw.execute('DELETE FROM calibre.data')
        rows = formats.execute('SELECT id, book, format, uncompressed_size, name FROM data').fetchall()
        raw.executemany('INSERT INTO calibre.data VALUES (?, ?, ?, ?, ?)', rows)
        raw.commit()
    ub.migrate_Database(h.session)
    h.session.expire_all()
    state = json.loads((fixture / 'state.json').read_text())
    h.session.add_all([ub.Downloads(user_id=state['user_id'], book_id=book_id)
                       for book_id in downloaded])
    h.session.commit()
    return state


@pytest.mark.parametrize('second_active', [True, False])
@pytest.mark.parametrize('token_kind', ['settled', 'stale'])
def test_tagged_delivery_survives_upgrade_with_another_active_or_retired_kobo(sync_harness, monkeypatch, second_active, token_kind):
    h = sync_harness
    state = _restore_tagged_database(h, downloaded=[1])
    second = h.session.get(ub.Device, state['second_device_id'])
    second.active = second_active
    h.session.commit()
    monkeypatch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
    token = state['token'] if token_kind == 'settled' else kobo.SyncToken.SyncToken().build_sync_token()
    response = h.sync(token)
    assert _entitlements(response) == [], response.get_json()
    # A paired Kobo with no delivery cannot inherit the speaking reader's row.
    new_reader = h.sync(state['token'], internal_device_id=second.id, raw_device_id='b' * 64)
    assert [set(item) for item in _entitlements(new_reader)] == [{'NewEntitlement'}]


@pytest.mark.parametrize('token_kind', ['settled', 'stale', 'none'])
def test_tagged_emission_the_reader_never_downloaded_is_announced_new_once(
        sync_harness, monkeypatch, token_kind):
    """A v4.1.43 row for a response that never arrived must not starve the book.

    v4.1.43 wrote its ledger row when it built the response, so a reader that
    lost the response, never saw the book and never downloaded it carries a
    row like a held book's; v4.1.43 then suppressed every resend (the
    real-upgrade gate's U43 book 906).  The upgrade must announce it New, once,
    whatever token the reader returns with.  Fails if the audit keeps a row
    that no download or reading report backs.
    """
    h = sync_harness
    state = _restore_tagged_database(h)
    monkeypatch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
    token = {
        'settled': state['token'],
        'stale': kobo.SyncToken.SyncToken().build_sync_token(),
        'none': None,
    }[token_kind]
    announced = []
    for _sync in range(3):
        response = h.sync(token)
        announced += [sorted(item)[0] for item in _entitlements(response)]
        token = response.headers[h.token_header]
    assert announced == ['NewEntitlement'], announced


@pytest.mark.parametrize('downloaded', [True, False])
@pytest.mark.parametrize('household_size', [1, 2])
def test_tagged_seed_copy_is_kept_only_with_delivery_evidence_and_never_swallows_a_deletion(
        sync_harness, monkeypatch, household_size, downloaded):
    """v4.1.43's copy of the flat history, on one reader or a household's two.

    The copy says only that some reader of the account was offered book 1.
    Kept when the account downloaded it (households included: no record
    says which reader downloaded, and clearing would resend the book to every
    reader that holds it), announced New to each reader when nobody did.
    The seed also marked deletion 111 delivered, and v4.1.43 recorded removal
    222 when it sent it, so each goes out once.
    """
    h = sync_harness
    state = _restore_tagged_database(h, 'seeded_only', downloaded=[1] if downloaded else [])
    if household_size == 1:
        h.session.query(ub.Device).filter(ub.Device.id == state['second_device_id']).delete()
        h.session.commit()
    monkeypatch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
    expected_new = 0 if downloaded else 1
    response = h.sync(state['token'])
    items = _entitlements(response)
    removed = [item['ChangedEntitlement']['BookEntitlement']['Id'] for item in items
               if 'ChangedEntitlement' in item]
    assert sorted(removed) == ['00000000-0000-0000-0000-000000000111',
                               '00000000-0000-0000-0000-000000000222'], items
    assert len([item for item in items if 'NewEntitlement' in item]) == expected_new
    # After acknowledging the first corrected page, no duplicate recovery.
    assert _entitlements(h.sync(response.headers[h.token_header])) == []
    assert _entitlements(h.sync(None)) == []
    if household_size == 2:
        second = h.sync(state['token'], internal_device_id=state['second_device_id'], raw_device_id='b' * 64)
        assert len([item for item in _entitlements(second) if 'NewEntitlement' in item]) == expected_new
        assert {item['ChangedEntitlement']['BookEntitlement']['Id'] for item in _entitlements(second)
                if 'ChangedEntitlement' in item} == {
                    '00000000-0000-0000-0000-000000000111',
                    '00000000-0000-0000-0000-000000000222',
                }


def test_failed_upgrade_classification_rolls_back_ledger_pruning(sync_harness, monkeypatch):
    """Failure after pruning must retain the recoverable pre-migration state."""
    from cps import kobo_sync_status
    h = sync_harness
    _restore_tagged_database(h, 'seeded_only')
    before = h.session.query(ub.KoboDeviceDeletedEntitlement).count()
    def fail_stamp(*args, **kwargs):
        raise RuntimeError('database write failed after pruning')
    monkeypatch.setattr(kobo_sync_status, 'mark_device_entitlement_classification', fail_stamp)
    assert kobo._migrate_device_entitlement_classification(h.user.id) is False
    assert h.session.query(ub.KoboDeviceDeletedEntitlement).count() == before
    assert {row.classification_version for row in h.session.query(ub.KoboDeviceEntitlementSeed)} == {0}
