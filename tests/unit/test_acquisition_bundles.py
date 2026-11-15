# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable artifact choice and private copy/import behavior, offline fixtures."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.unit.test_acquisition_usenet import repo, spec, s, usenet_worker_fixture


def module(name):
    return importlib.import_module(spec.name + '.' + name)


def waiting(repository, tmp_path):
    client, connection, payload, job = usenet_worker_fixture((repository, [1000.]), tmp_path)
    claim = repository.claim()
    repository.advance(job.id, claim.token, 'queued', 'resolving')
    repository.advance(job.id, claim.token, 'resolving', 'downloading')
    repository.begin_submission(job.id, claim.token)
    repository.record_external(job.id, claim.token, 'owned')
    candidates = [dict(id=str(i)*64, name=f'Book {i}.epub', relative_path=f'owned/book{i}.epub',
        media_type='application/epub+zip', size=10, sha256=str(i)*64) for i in (1, 2)]
    repository.await_choices(job.id, claim.token, {'generation':'generation1','candidates':candidates})
    return client, connection, payload, job, candidates


def test_choices_durable_encrypted_private_and_not_polled(repo, tmp_path):
    repository, _ = repo
    client, connection, payload, job, candidates = waiting(repository, tmp_path)
    assert repository.get_job(1, job.id).state == 'awaiting_selection'
    assert repository.claim() is None
    public = repository.bundle_choices(1, job.id)
    assert public['generation'] == 'generation1'
    assert len(public['candidates']) == 2
    assert 'relative_path' not in json.dumps(public) and 'sha256' not in json.dumps(public)
    with repository.engine.connect() as conn:
        encrypted = conn.execute(select(repository.tables.manifests)).mappings().one()
    assert b'owned/book1.epub' not in encrypted['payload_ciphertext']
    with pytest.raises(s.NotFound): repository.bundle_choices(2, job.id)
    with pytest.raises(s.NotFound): repository.select_book(2, job.id, 'generation1', candidates[0]['id'])


def test_first_later_and_catalog_replay_keep_one_remote_attempt(repo, tmp_path):
    repository, _ = repo
    client, connection, payload, job, candidates = waiting(repository, tmp_path)
    first = repository.select_book(1, job.id, 'generation1', candidates[0]['id'], requires_approval=False)
    assert first.id == job.id and first.state == 'queued'
    second = repository.select_book(1, job.id, 'generation1', candidates[1]['id'], requires_approval=True)
    assert second.id != first.id and second.state == 'awaiting_approval'
    assert repository.select_book(1, job.id, 'generation1', candidates[1]['id']).id == second.id
    assert repository.select_book(1, job.id, 'generation1', candidates[0]['id']).id == first.id
    again = repository.create_job(1, repository.create_offer(1, connection.id, payload), 'fresh-click', requires_approval=False)
    assert again.id == first.id
    # Settle first before approving the independently pending sibling.
    claim = repository.claim()
    repository.advance(claim.job.id, claim.token, 'queued', 'failed', error_code='test_failure')
    repository.approve(second.id, admin_actor=9)
    sibling = repository.claim()
    assert sibling.job.id == second.id
    assert repository.begin_submission(second.id, sibling.token) is False
    assert repository.submission_identity(second.id, sibling.token)[0] == 'owned'


@pytest.mark.parametrize('change', ['generation', 'foreign_candidate', 'cancel', 'client_disabled', 'catalog_disabled'])
def test_choice_fences(repo, tmp_path, change):
    repository, _ = repo
    client, connection, payload, job, candidates = waiting(repository, tmp_path)
    generation, candidate = 'generation1', candidates[0]['id']
    if change == 'generation': generation = 'stale'
    if change == 'foreign_candidate': candidate = 'f'*64
    if change == 'cancel': repository.request_cancel(1, job.id)
    if change == 'client_disabled': repository.set_connection_enabled(client.id, False)
    if change == 'catalog_disabled': repository.set_connection_enabled(connection.id, False)
    with pytest.raises((s.Conflict, s.NotFound)):
        repository.select_book(1, job.id, generation, candidate, requires_approval=False)
    assert repository.claim() is None


def test_worker_wait_choose_later_import_and_changed_bytes_never_fallback(repo, tmp_path):
    repository, _ = repo
    w, h = module('worker'), module('http')
    client, connection, payload, job = usenet_worker_fixture(repo, tmp_path)
    owned = tmp_path / 'owned'; owned.mkdir()
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/sample_books/test_minimal_valid.epub'
    first = owned/'one.epub'; first.write_bytes(fixture.read_bytes())
    second = owned/'two.pdf'; second.write_bytes(b'%PDF-1.4\nOriginal bundle PDF\n%%EOF\n')
    original = {p.name:p.read_bytes() for p in owned.iterdir()}
    submits = []
    class Client:
        def __init__(self, *a, **kw): pass
        def submit(self, *a, **kw): submits.append(1); return 'owned'
        def find(self, *a, **kw):
            return {'nzo_id':'owned','status':'Completed','loaded':False,'storage':'/downloads/owned'}
    ingest = tmp_path/'ingest'; ingest.mkdir()
    worker = w.AcquisitionWorker(repository, tmp_path/'staging', ingest, allowed=lambda _:True,
        transfer=lambda url,*a,**kw:h.FetchedDocument(b'<nzb><file/></nzb>',url,'text/xml'), client_factory=Client)
    assert worker.run_once().state == 'awaiting_selection'
    assert worker.run_once() is None
    manifest = repository.bundle_choices(1, job.id)
    choice = next(x for x in manifest['candidates'] if x['name']=='two.pdf')
    selected = repository.select_book(1, job.id, manifest['generation'], choice['id'], requires_approval=False)
    assert worker.run_once().state == 'importing'
    published = next(p for p in ingest.iterdir() if p.suffix=='.pdf')
    assert published.read_bytes() == original['two.pdf']
    # Prove a second selection stays independent even after receipt success.
    sidecar = json.loads(Path(str(published)+'.cwa.json').read_text())
    receipt = module('staging').digest(published)
    repository.finalize_import(job.id, sidecar['publication_token'],
        s.ImportOutcome(receipt, receipt, (7,), 'imported'), staging_key=sidecar['staging_key'],
        finalize_membership=lambda conn,job,outcome:None)
    choice = next(x for x in manifest['candidates'] if x['name']=='one.epub')
    sibling = repository.select_book(1, job.id, manifest['generation'], choice['id'], requires_approval=False)
    first.write_bytes(b'changed selected source')
    result = worker.run_once()
    assert result.id == sibling.id and result.state == 'failed' and result.error_code == 'artifact_changed'
    assert repository.get_receipt(1, sibling.id) is None
    assert len(submits)==1 and second.read_bytes()==original['two.pdf']


def test_cancelling_first_selected_book_does_not_cancel_other_choices(repo,tmp_path):
    repository, _ = repo
    client, connection, payload, job, candidates = waiting(repository,tmp_path)
    first=repository.select_book(1,job.id,'generation1',candidates[0]['id'],requires_approval=False)
    repository.request_cancel(1,first.id)
    second=repository.select_book(1,job.id,'generation1',candidates[1]['id'],requires_approval=False)
    assert second.id!=first.id and second.state=='queued'
    assert repository.get_job(1,first.id).state=='cancelled'
    assert repository.get_job(1,first.id).bundle_selectable is True


def test_concurrent_repeated_choices_create_only_two_artifact_jobs(repo,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    repository, _ = repo
    client, connection, payload, job, candidates = waiting(repository,tmp_path)
    choices=[candidates[i%2]['id'] for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda candidate:repository.select_book(1,job.id,'generation1',candidate,requires_approval=False).id,choices))
    assert len(set(ids))==2
    assert len(repository.list_jobs(1))==2
    assert ids[::2]==[ids[0]]*4 and ids[1::2]==[ids[1]]*4
