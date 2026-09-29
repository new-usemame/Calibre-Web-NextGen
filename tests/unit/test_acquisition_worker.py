# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable DB claims → real staged bytes → watched publication boundaries."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest
from sqlalchemy import MetaData, create_engine

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_acquisition_worker_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec); sys.modules[spec.name] = package; spec.loader.exec_module(package)
w = importlib.import_module(spec.name + '.worker')
s = importlib.import_module(spec.name + '.storage')
k = importlib.import_module(spec.name + '.secrets')
c = importlib.import_module(spec.name + '.catalog')
h = importlib.import_module(spec.name + '.http')


@pytest.fixture
def fixture(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'))
    metadata = MetaData(); tables = s.define_tables(metadata); metadata.create_all(engine)
    now = [1000.0]; repo = s.Repository(engine, tables, k.SecretBox(b'x' * 32), clock=lambda: now[0])
    connection = repo.create_connection('Books', 'opds', c.connection_config({'endpoint': 'https://example.org/feed'}), enabled=True)
    offer = repo.create_offer(1, connection.id, {'kind': 'acquisition', 'href': 'https://example.org/book.pdf', 'media_type': 'application/pdf'})
    job = repo.create_job(1, offer, 'click', requires_approval=False)
    ingest = tmp_path / 'ingest'; ingest.mkdir()
    calls = []
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        calls.append(url); checkpoint()
        destination.write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
        return h.DownloadedFile(destination, destination.stat().st_size, w.digest(destination), 'application/pdf')
    worker = w.AcquisitionWorker(repo, tmp_path / 'staging', ingest, allowed=lambda owner: owner == 1, transfer=transfer)
    yield repo, worker, job, now, calls, ingest
    engine.dispose()


def test_download_publication_restart_and_receipt_preserve_identity(fixture):
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    files = list(ingest.glob('*.pdf')); assert len(files) == 1 and len(calls) == 1
    manifest = json.loads(Path(str(files[0]) + '.cwa.json').read_text())
    assert manifest['job_id'] == job.id and 'user_id' not in manifest
    private_source = next(worker.staging_dir.rglob('source.part'))
    original = private_source.read_bytes()
    now[0] += 61
    second_worker = w.AcquisitionWorker(repo, worker.staging_dir, ingest, allowed=lambda _: True,
        transfer=lambda *a, **k: pytest.fail('Restart redownloaded published source'))
    assert second_worker.run_once().state == 'importing'
    assert files[0].read_bytes() == original and private_source.read_bytes() == original
    intent, permit = repo.publication_intent(job.id, manifest['publication_token'], manifest['staging_key'])
    outcome = s.ImportOutcome(permit.source_sha256, permit.source_sha256, (42,))
    # This is the repository seam only; real Calibre/member validation has its
    # separate integration suite and is not substituted by this callback.
    repo.finalize_import(job.id, permit.token, outcome, staging_key=permit.staging_key, finalize_membership=lambda *args: None)
    assert repo.get_job(1, job.id).state == 'imported'
    assert second_worker.run_once() is None
    assert not private_source.exists() and not (worker.staging_dir / job.id).exists()


def test_cancel_or_revoked_access_prevents_any_download(fixture):
    repo, worker, job, now, calls, ingest = fixture
    worker.allowed = lambda _: False
    result = worker.run_once()
    assert result.state == 'failed' and result.error_code == 'access_revoked'
    assert calls == [] and not list(ingest.iterdir())
    repo.retry(1, job.id); worker.allowed = lambda _: True
    repo.request_cancel(1, job.id)
    assert worker.run_once() is None and repo.get_job(1, job.id).state == 'cancelled'
    assert calls == []


def test_cancellation_during_transfer_never_publishes_partial(fixture):
    repo, worker, job, now, calls, ingest = fixture
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        destination.write_bytes(b'partial')
        repo.request_cancel(1, job.id)
        checkpoint()
        pytest.fail('Cancelled stream continued')
    worker.transfer = transfer
    assert worker.run_once().state == 'cancelled'
    assert not list(ingest.iterdir())


def test_lost_worker_cannot_publish_or_fail_replacement_claim(fixture):
    repo, worker, job, now, calls, ingest = fixture
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        destination.write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
        now[0] += 61
        replacement = repo.claim()
        assert replacement is not None
        checkpoint()
        return h.DownloadedFile(destination, destination.stat().st_size, w.digest(destination), 'application/pdf')
    worker.transfer = transfer
    result = worker.run_once()
    assert result.state == 'downloading'
    assert not list(ingest.iterdir())


def test_pause_leaves_durable_job_for_later_resume_without_publication(fixture):
    repo, worker, job, now, calls, ingest = fixture
    worker.enabled = lambda: False
    assert worker.run_once() is None and calls == []
    enabled = [True]; worker.enabled = lambda: enabled[0]
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        enabled[0] = False; checkpoint()
    worker.transfer = transfer
    assert worker.run_once().state == 'downloading'
    assert not list(ingest.iterdir())


def test_reclaim_between_cancellation_detection_and_cleanup_preserves_new_worker(fixture, monkeypatch):
    repo, worker, job, now, calls, ingest = fixture
    original = repo.advance
    def advance(job_id, token, expected, target, **kwargs):
        if target == 'cancelled':
            now[0] += 61
            assert repo.claim() is not None
        return original(job_id, token, expected, target, **kwargs)
    monkeypatch.setattr(repo, 'advance', advance)
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        repo.request_cancel(1, job.id); checkpoint()
    worker.transfer = transfer
    assert worker.run_once().state == 'downloading'
    assert not list(ingest.iterdir())


def test_invalid_download_is_not_published_or_left_as_abandoned_private_copy(fixture):
    repo, worker, job, now, calls, ingest = fixture
    def transfer(url, policy, *, destination, checkpoint, **kwargs):
        destination.write_bytes(b'<html>Authentication required</html>')
        return h.DownloadedFile(destination, destination.stat().st_size, w.digest(destination), 'text/html')
    worker.transfer = transfer
    assert worker.run_once().state == 'failed'
    assert not list(ingest.iterdir())
    assert not list(worker.staging_dir.rglob('source.part'))


def test_stale_attempt_cannot_delete_source_adopted_by_publishing_replacement(fixture, monkeypatch):
    repo, worker, job, now, calls, ingest = fixture
    original = repo.prepare_publication
    permits = []
    def intercepted(job_id, token, publication_token):
        now[0] += 61
        replacement = repo.claim()
        permits.append(original(job_id, replacement.token, publication_token))
        repo.advance(job_id, replacement.token, 'publishing', 'failed', error_code='publication_io')
        return original(job_id, token, publication_token)
    monkeypatch.setattr(repo, 'prepare_publication', intercepted)
    assert worker.run_once().state == 'failed'
    permit = permits[0]
    repo.publication_intent(job.id, permit.token, permit.staging_key)
    private = worker.staging_dir / job.id / permit.staging_key
    assert (private / 'source.part').is_file()
    assert (private / 'publication.token').read_text() == permit.token


def test_completed_cleanup_is_not_starved_by_retained_directories(fixture, monkeypatch):
    import uuid
    repo, worker, job, now, calls, ingest = fixture
    worker.run_once()
    source = next(worker.staging_dir.rglob('source.part'))
    manifest = json.loads(next(ingest.glob('*.cwa.json')).read_text())
    _, permit = repo.publication_intent(job.id, manifest['publication_token'], manifest['staging_key'])
    repo.finalize_import(job.id, permit.token, s.ImportOutcome(permit.source_sha256, permit.source_sha256, (42,)),
        staging_key=permit.staging_key, finalize_membership=lambda *args: None)
    retained = [worker.staging_dir / str(uuid.uuid4()) for _ in range(51)]
    for path in retained: path.mkdir()
    original = Path.iterdir
    def ordered(path):
        return iter(retained + [worker.staging_dir / job.id]) if path == worker.staging_dir else original(path)
    monkeypatch.setattr(Path, 'iterdir', ordered)
    worker.enabled = lambda: False
    assert worker.run_once() is None
    assert not source.exists() and all(path.exists() for path in retained)


@pytest.mark.parametrize('during_transfer', [False, True])
def test_current_format_policy_blocks_download_or_publication(fixture, during_transfer):
    repo, worker, job, now, calls, ingest = fixture
    permitted = [during_transfer]
    worker.media_allowed = lambda media: permitted[0]
    original = worker.transfer
    def transfer(*args, **kwargs):
        result = original(*args, **kwargs)
        permitted[0] = False
        return result
    worker.transfer = transfer
    result = worker.run_once()
    assert result.state == 'failed' and result.error_code == 'format_not_allowed'
    assert len(calls) == int(during_transfer)
    assert not list(ingest.iterdir())


@pytest.mark.parametrize('header,delay', [(120, 120), (None, 60), (0, 0), (999999, 86400)])
def test_source_busy_manual_retry_respects_persisted_delay(fixture, header, delay):
    repo, worker, job, now, calls, ingest = fixture
    original = worker.transfer
    def busy(url, *args, **kwargs):
        calls.append(url)
        raise h.TransportError('source_busy', retry_after=header)
    worker.transfer = busy
    assert worker.run_once().error_code == 'source_busy'
    assert worker.run_once() is None  # failure never automatically requeues
    repo.retry(1, job.id)
    with pytest.raises(s.Conflict): repo.retry(1, job.id)
    worker.transfer = original
    if delay:
        now[0] += delay - 0.01
        assert worker.run_once() is None and len(calls) == 1
        now[0] += 0.01
    assert worker.run_once().state == 'importing'
    assert len(calls) == 2


def test_backoff_does_not_allow_revoked_retry(fixture):
    repo, worker, job, now, calls, ingest = fixture
    def busy(*args, **kwargs): raise h.TransportError('source_busy', retry_after=120)
    worker.transfer = busy
    worker.run_once(); repo.retry(1, job.id)
    worker.allowed = lambda _: False
    now[0] += 120
    assert worker.run_once().error_code == 'access_revoked'
    assert not list(ingest.iterdir())
