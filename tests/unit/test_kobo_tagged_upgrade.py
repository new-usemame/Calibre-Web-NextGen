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


@pytest.fixture(autouse=True)
def tagged_render_settings(monkeypatch):
    # The tagged snapshot emitted bare cover IDs. Config-writing tests may
    # reload the live default (padding enabled) into the process-wide config;
    # that is a real payload change, not an unchanged upgrade replay.
    monkeypatch.setattr(kobo.config, 'config_kobo_cover_padding_enabled', False, raising=False)



def _restore_tagged_database(h, scenario="emitted"):
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
    return json.loads((fixture / 'state.json').read_text())


@pytest.mark.parametrize('second_active', [True, False])
@pytest.mark.parametrize('token_kind', ['settled', 'stale'])
def test_tagged_delivery_survives_upgrade_with_another_active_or_retired_kobo(sync_harness, monkeypatch, second_active, token_kind):
    h = sync_harness
    state = _restore_tagged_database(h)
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


@pytest.mark.parametrize('household_size', [1, 2])
def test_tagged_seed_cannot_swallow_outstanding_deletion_or_claim_second_reader_receipt(
        sync_harness, monkeypatch, household_size):
    h = sync_harness
    state = _restore_tagged_database(h, 'seeded_only')
    if household_size == 1:
        h.session.query(ub.Device).filter(ub.Device.id == state['second_device_id']).delete()
        h.session.commit()
    monkeypatch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
    # Equal-resolution timestamps carry no ordering proof either.
    guessed = h.session.query(ub.KoboDeviceDeletedEntitlement).filter_by(
        device_id=state['device_id'], book_uuid='00000000-0000-0000-0000-000000000111').one()
    guessed.updated_at = h.session.get(ub.KoboDeviceEntitlementSeed, state['device_id']).seeded_at
    h.session.commit()
    response = h.sync(state['token'])
    items = _entitlements(response)
    removed = [item['ChangedEntitlement']['BookEntitlement']['Id'] for item in items
               if 'ChangedEntitlement' in item]
    assert removed == ['00000000-0000-0000-0000-000000000111'], items
    # The old writer actually emitted 222 after seeding; do not replay it.
    assert len([item for item in items if 'NewEntitlement' in item]) == (household_size - 1)
    # After acknowledging the first corrected page, no duplicate recovery.
    assert _entitlements(h.sync(response.headers[h.token_header])) == []
    if household_size == 2:
        second = h.sync(state['token'], internal_device_id=state['second_device_id'], raw_device_id='b' * 64)
        assert len([item for item in _entitlements(second) if 'NewEntitlement' in item]) == 1
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
