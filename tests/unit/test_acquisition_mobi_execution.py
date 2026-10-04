# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual request/worker fences for a newly opted-in direct source format."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests.unit.test_acquisition_api import api
from tests.unit.test_acquisition_worker import fixture, w, c, h

MOBI = 'application/x-mobipocket-ebook'
ROOT = Path(__file__).resolve().parents[1]


def test_admin_mobi_opt_in_roundtrips_and_revokes_through_existing_connection_api(api):
    client, repo, actor, _, _, _, _ = api
    actor.id = 1
    created = client.post('/api/v1/admin/acquisition/connections', json={
        'label': 'Original legal MOBI catalog', 'adapter': 'opds',
        'config': {'endpoint': 'https://catalog.example.invalid/opds', 'allow_mobi': True},
    })
    assert created.status_code == 201
    identifier = created.get_json()['id']
    loaded = client.get('/api/v1/admin/acquisition/connections/' + identifier).get_json()
    assert loaded['config']['allow_mobi'] is True and not loaded['enabled']
    assert 'secret' not in loaded['config']
    edited = client.patch('/api/v1/admin/acquisition/connections/' + identifier, json={
        'config': {'allow_mobi': False}, 'expected_revision': loaded['revision'],
    })
    assert edited.status_code == 200
    reloaded = client.get('/api/v1/admin/acquisition/connections/' + identifier).get_json()
    assert reloaded['config']['allow_mobi'] is False
    assert reloaded['revision'] > loaded['revision']


@pytest.mark.parametrize('opt_in,formats,status', [
    (True, 'epub,pdf,mobi', 202), (True, '', 202),
    (False, 'epub,pdf,mobi', 400), (True, 'epub,pdf', 400)])
def test_actual_request_needs_both_catalog_choice_and_current_global_policy(api, opt_in, formats, status):
    client, repo, actor, module, _connection, _offer, database = api
    config = module.connection_config({'endpoint': 'https://catalog.invalid', 'allow_mobi': opt_in})
    connection = repo.create_connection('MOBI catalog', 'opds', config, enabled=True)
    offer = repo.create_offer(2, connection.id, {'kind': 'acquisition', 'href': 'https://catalog.invalid/book.mobi', 'media_type': MOBI})
    with repo.engine.begin() as con:
        con.execute(text('UPDATE settings SET config_upload_formats=:formats'), {'formats': formats})
    response = client.post('/api/v1/acquisition/jobs', json={'connection_id': connection.id, 'offer_id': offer, 'idempotency_key': 'mobi'})
    assert response.status_code == status
    if status == 202:
        assert response.get_json()['state'] == 'awaiting_approval'
        assert 'book.mobi' not in response.get_data(as_text=True)
    else:
        assert repo.list_jobs(2) == ()


@pytest.mark.parametrize('formats,expected', [('', ['MOBI', 'EPUB', 'NZB', 'Torrent']),
    ('epub,pdf', ['EPUB', 'NZB', 'Torrent']), ('mobi', ['MOBI'])])
def test_catalog_api_filters_direct_and_client_capabilities_separately(api, monkeypatch, formats, expected):
    client, repo, actor, module, connection, _offer, database = api
    with repo.engine.begin() as con:
        con.execute(text('UPDATE settings SET config_upload_formats=:formats'), {'formats': formats})
    page = {'publications': [{'title': 'Policy fixture', 'offers': [
        {'format': name, 'offer_id': name} for name in ['MOBI', 'EPUB', 'NZB', 'Torrent']]}]}
    monkeypatch.setattr(module, 'CatalogService', lambda *a, **k: SimpleNamespace(browse=lambda *a, **k: page))
    monkeypatch.setattr(module, '_run_private_blocking', lambda operation: operation())
    response = client.get('/api/v1/acquisition/catalog', query_string={'connection': connection.id})
    assert response.status_code == 200
    assert [x['format'] for x in response.get_json()['publications'][0]['offers']] == expected


def test_mobi_worker_stages_correct_name_and_real_intent_accepts_it(fixture):
    repo, worker, _job, now, calls, ingest = fixture
    # Settle the unrelated fixture job before driving the owning one.
    repo.request_cancel(1, _job.id)
    connection = repo.create_connection('MOBI', 'opds', c.connection_config({'endpoint': 'https://example.org/feed', 'allow_mobi': True}), enabled=True)
    offer = repo.create_offer(1, connection.id, {'kind': 'acquisition', 'href': 'https://example.org/original.mobi', 'media_type': MOBI})
    job = repo.create_job(1, offer, 'mobi', requires_approval=False)
    original = (ROOT / 'fixtures/sample_books/test_original_direct.mobi').read_bytes()
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        calls.append(url); checkpoint(); destination.write_bytes(original)
        return h.DownloadedFile(destination, len(original), w.digest(destination), MOBI)
    worker.transfer = transfer
    result = worker.run_once()
    assert result.id == job.id and result.state == 'importing'
    books = list(ingest.glob('*.mobi')); assert len(books) == 1 and books[0].read_bytes() == original
    manifest = json.loads(Path(str(books[0]) + '.cwa.json').read_text())
    import importlib
    intent_module = importlib.import_module(w.__package__ + '.ingest')
    intent = intent_module.load_intent(repo, books[0], ingest, manifest)
    assert intent.job_id == job.id and intent.source_path == books[0]
    assert intent.source_sha256 == w.digest(books[0])


@pytest.mark.parametrize('revoke', ['catalog', 'global'])
def test_mobi_worker_refuses_missing_opt_in_or_revoked_format_before_transfer(fixture, revoke):
    repo, worker, unrelated, now, calls, ingest = fixture
    repo.request_cancel(1, unrelated.id)
    config = c.connection_config({'endpoint': 'https://example.org/feed', 'allow_mobi': revoke != 'catalog'})
    connection = repo.create_connection('MOBI', 'opds', config, enabled=True)
    offer = repo.create_offer(1, connection.id, {'kind': 'acquisition', 'href': 'https://example.org/original.mobi', 'media_type': MOBI})
    job = repo.create_job(1, offer, 'mobi', requires_approval=False)
    worker.media_allowed = lambda media: revoke != 'global'
    worker.transfer = lambda *args, **kwargs: pytest.fail('Refused format reached transfer')
    result = worker.run_once()
    assert result.id == job.id and result.state == 'failed'
    assert result.error_code == ('format_not_allowed' if revoke == 'global' else 'unsupported_offer')
    assert not list(ingest.iterdir()) and calls == []
