# SPDX-License-Identifier: GPL-3.0-or-later
"""Run from clean v4.1.43 (bdead55920) to freeze its actual sync state.

PYTHONPATH=. python /path/to/this/generate_v4_1_43.py OUTPUT_DIRECTORY
Uses the tagged handler, models and test transport; no current code is loaded.
The fixture account is synthetic. Clocks remain exactly as recorded by the old
writer so its seed/emission ordering is preserved rather than invented.
"""
from datetime import datetime
import gzip
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import tempfile

import pytest

expected = 'bdead55920e066ff529da0865c712b5dcdf7cfd3'
actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
if actual != expected:
    raise SystemExit(f'Run this generator from v4.1.43 ({expected}), not {actual}')

runpy.run_path('tests/unit/conftest.py')
from tests.unit.test_1925_kobo_sync_dedownload import sync_harness, _entitlements
from cps import kobo, ub, config_sql

output = Path(sys.argv[1])
for scenario in ('emitted', 'seeded_only'):
    target = output / scenario
    target.mkdir(parents=True, exist_ok=True)
    patch = pytest.MonkeyPatch()
    harness = sync_harness.__wrapped__(patch)
    h = next(harness)
    try:
        patch.setattr(kobo.config, 'config_kobo_suppress_replayed_entitlements', True)
        patch.setattr(kobo.config, 'config_kobo_cover_padding_enabled', False, raising=False)
        config_sql._Settings.__table__.create(h.session.bind, checkfirst=True)
        h.session.add(ub.User(id=h.user.id, name='Upgrade Reader',
                             email='upgrade@example.org', password='', role=16))
        second = ub.Device(user_id=h.user.id, kind='kobo', display_name='Retired Kobo',
                           model='Kobo Libra Colour', active=False, created_by='auto')
        h.session.add(second)
        h.session.commit()
        token = None
        if scenario == 'seeded_only':
            h.session.add(ub.KoboSyncedBooks(user_id=h.user.id, book_id=h.book.id,
                                           book_uuid=str(h.book.uuid)))
            h.session.add(ub.KoboDeletedBook(user_id=h.user.id,
                book_uuid='00000000-0000-0000-0000-000000000111', deleted_at=datetime(2026, 8, 20)))
            h.session.commit()
            token = kobo.SyncToken.SyncToken(
                books_last_modified=datetime(2030, 1, 1), books_last_created=datetime(2030, 1, 1),
                archive_last_modified=datetime(2030, 1, 1)).build_sync_token()
        first = h.sync(token)
        assert len(_entitlements(first)) == (1 if scenario == 'emitted' else 0)
        token = first.headers[h.token_header]
        stable = h.sync(token)
        token = stable.headers[h.token_header]
        assert _entitlements(stable) == []
        if scenario == 'seeded_only':
            # Unlike the initial copied tombstone, this deletion is emitted to
            # the first reader after its durable seed marker already exists.
            h.session.add(ub.KoboDeletedBook(user_id=h.user.id,
                book_uuid='00000000-0000-0000-0000-000000000222', deleted_at=datetime(2026, 8, 21)))
            h.session.commit()
            stale = kobo.SyncToken.SyncToken(
                books_last_modified=datetime(2030, 1, 1), books_last_created=datetime(2030, 1, 1)).build_sync_token()
            deletion = h.sync(stale)
            assert len(_entitlements(deletion)) == 1
            assert _entitlements(deletion)[0]['ChangedEntitlement']['BookEntitlement']['Id'].endswith('222')
            token = deletion.headers[h.token_header]
        h.session.commit()
        raw = h.session.connection().connection.driver_connection
        for name in ('main', 'calibre'):
            with tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / 'capture.db'
                destination = sqlite3.connect(path)
                raw.backup(destination, name=name)
                destination.close()
                (target / f'{name}.db.gz').write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        (target / 'state.json').write_text(json.dumps({
            'source_tag': 'v4.1.43', 'source_commit': 'bdead55920', 'scenario': scenario,
            'device_id': h.device.id, 'second_device_id': second.id,
            'book_id': h.book.id, 'user_id': h.user.id, 'token': token,
        }, indent=2)+'\n')
    finally:
        harness.close()
        patch.undo()
