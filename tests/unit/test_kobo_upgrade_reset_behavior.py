# SPDX-License-Identifier: GPL-3.0-or-later
"""Tagged upgrade evidence must survive audits, but never defeat an explicit reset."""
import pytest
from cps import kobo, ub
from tests.unit.test_1925_kobo_sync_dedownload import sync_harness, _entitlements
from tests.unit.test_kobo_tagged_upgrade import _restore_tagged_database
pytestmark = pytest.mark.unit

@pytest.mark.parametrize('basis', [None, 'acknowledged-modern-basis'])
def test_equal_book_timestamp_is_ambiguous_unless_modern_provenance(sync_harness, basis):
    h = sync_harness
    state = _restore_tagged_database(h, 'seeded_only')
    row = h.session.query(ub.KoboDeviceBookEntitlement).filter_by(device_id=state['device_id']).one()
    row.updated_at = h.session.get(ub.KoboDeviceEntitlementSeed, state['device_id']).seeded_at
    row.change_basis = basis
    h.session.commit()
    assert kobo._migrate_device_entitlement_classification(h.user.id)
    h.session.commit()
    surviving = h.session.query(ub.KoboDeviceBookEntitlement).filter_by(device_id=state['device_id']).count()
    assert surviving == int(basis is not None)


def test_repeated_schema_and_classification_migration_preserves_tagged_emission(sync_harness):
    h = sync_harness
    state = _restore_tagged_database(h)
    assert kobo._migrate_device_entitlement_classification(h.user.id)
    h.session.commit()
    before = [(r.device_id, r.book_id, r.fingerprint, r.updated_at) for r in h.session.query(ub.KoboDeviceBookEntitlement)]
    ub.migrate_Database(h.session)
    assert kobo._migrate_device_entitlement_classification(h.user.id)
    h.session.commit()
    after = [(r.device_id, r.book_id, r.fingerprint, r.updated_at) for r in h.session.query(ub.KoboDeviceBookEntitlement)]
    assert before == after
    assert _entitlements(h.sync(state['token'])) == []


def test_failed_response_commit_retries_tagged_pending_delete(sync_harness, monkeypatch):
    h = sync_harness
    state = _restore_tagged_database(h, 'seeded_only')
    original = ub.session_commit
    def fail_commit():
        h.session.rollback()
        return False
    monkeypatch.setattr(ub, 'session_commit', fail_commit)
    from werkzeug.exceptions import ServiceUnavailable
    with pytest.raises(ServiceUnavailable):
        h.sync(state['token'])
    assert {r.classification_version for r in h.session.query(ub.KoboDeviceEntitlementSeed)} == {0}
    assert h.session.query(ub.KoboDeviceDeletedEntitlement).filter_by(device_id=state['device_id'], book_uuid='00000000-0000-0000-0000-000000000111').count() == 1
    monkeypatch.setattr(ub, 'session_commit', original)
    retry = h.sync(state['token'])
    assert retry.status_code == 200
    removals = [r['ChangedEntitlement']['BookEntitlement']['Id'] for r in _entitlements(retry) if 'ChangedEntitlement' in r]
    assert removals == ['00000000-0000-0000-0000-000000000111']


@pytest.mark.parametrize('action', ['fullsync', 'resend'])
@pytest.mark.parametrize('seed_exists', [True, False])
def test_explicit_reset_reannounces_previously_read_book_new(sync_harness, monkeypatch, action, seed_exists):
    from cps import admin
    from datetime import datetime
    h = sync_harness
    state = _restore_tagged_database(h)
    h.session.add(ub.DeviceReadingPosition(device_id=state['device_id'], book_id=state['book_id'], client_modified_at=datetime(2026, 8, 30), progress_percent=42))
    if not seed_exists:
        h.session.query(ub.KoboDeviceEntitlementSeed).delete()
    h.session.commit()
    monkeypatch.setattr(admin, '_', lambda value: value)
    monkeypatch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
    monkeypatch.setattr(admin, 'calibre_db', kobo.calibre_db)
    with h.app.test_request_context('/ajax/fullsync/17', method='POST'):
        if action == 'fullsync':
            response = admin.do_full_kobo_sync(h.user.id)
        else:
            response = admin.do_kobo_resend(h.user.id, state['book_id'])
        assert response.status_code == 200
    result = h.sync(state['token'])
    assert [set(item) for item in _entitlements(result)] == [{'NewEntitlement'}], _entitlements(result)
    assert h.session.query(ub.DeviceReadingPosition).filter_by(device_id=state['device_id'], book_id=state['book_id']).one().progress_percent == 42
