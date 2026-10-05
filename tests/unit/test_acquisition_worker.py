# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable DB claims → real staged bytes → watched publication boundaries."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
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
    def intercepted(job_id, token, publication_token, **kwargs):
        now[0] += 61
        replacement = repo.claim()
        permits.append(original(job_id, replacement.token, publication_token, **kwargs))
        repo.advance(job_id, replacement.token, 'publishing', 'failed', error_code='publication_io')
        return original(job_id, token, publication_token, **kwargs)
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


def _book(ingest):
    books = sorted(p for p in ingest.iterdir() if p.suffix in ('.pdf', '.epub'))
    return books[0] if books else None


def _staging_key(repo, job):
    from sqlalchemy import select
    with repo.engine.connect() as connection:
        return connection.execute(select(repo.tables.jobs.c.staging_key).where(
            repo.tables.jobs.c.id == job.id)).scalar()


def _refuse_publication(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('re-published a book the ingest service already owns')
    monkeypatch.setattr(w, 'publish', refuse)


def _run_ingest_service(book, exit_code):
    """Drive the production shell service with a processor exit status."""
    root = Path(__file__).resolve().parents[2]
    run_script = root / 'root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run'
    scratch = book.parent.parent / f'service-test-{exit_code}'
    scratch.mkdir()
    stub = scratch / 'processor.sh'
    # 124 is not faked: the stub outlives a one-second budget and the
    # service's own `timeout` produces it, exactly as a hung conversion does.
    stub.write_text('#!/usr/bin/env bash\n'
                    '[ "$PROCESSOR_EXIT_CODE" = 124 ] && exec sleep 30\n'
                    'exit "$PROCESSOR_EXIT_CODE"\n')
    stub.chmod(0o755)
    post_batch = scratch / 'post-batch.sh'
    post_batch.write_text('#!/usr/bin/env bash\nexit 0\n')
    post_batch.chmod(0o755)
    env = {
        **os.environ,
        'WATCH_FOLDER': str(book.parent),
        'CWA_INGEST_SERVICE_TEST_MODE': '1',
        'CWA_INGEST_PROCESSING_DIR': str(scratch / 'processing'),
        'CWA_INGEST_RECENT_DIR': str(scratch / 'recent'),
        'CWA_INGEST_RETRY_QUEUE': str(scratch / 'retry_queue'),
        'CWA_INGEST_STATUS_FILE': str(scratch / 'status'),
        'CWA_INGEST_BATCH_DIRTY_FILE': str(scratch / 'batch_dirty'),
        'CWA_INGEST_BATCH_LAST_SUCCESS_FILE': str(scratch / 'batch_success'),
        'CWA_INGEST_POST_BATCH_CMD': str(post_batch),
        'CWA_INGEST_PROCESSOR_CMD': str(stub),
        'PROCESSOR_EXIT_CODE': str(exit_code),
    }
    script = textwrap.dedent(f'''
        set -euo pipefail
        mkdir -p "$CWA_INGEST_PROCESSING_DIR" "$CWA_INGEST_RECENT_DIR"
        source "{run_script}" >/dev/null
        get_timeout_from_db() {{ echo 1; }}
        handle_event "{book}" "CLOSE_WRITE" >/dev/null 2>&1 || true
    ''')
    subprocess.run(['bash', '-c', script], env=env, check=True)
    marker = Path(str(book) + '.cwa.failed.json')
    assert marker.is_file(), (
        f'terminal processor exit {exit_code} produced no acquisition failure signal')
    return marker


def test_safety_timeout_fails_the_job_instead_of_recreating_the_book(fixture, monkeypatch):
    """Exit 124 copies the book to the failed folder and deletes it.

    Republishing recreated it on the next claim, so the processor timed out
    again, and the failed folder filled with one copy every few seconds.
    """
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    published = _book(ingest)
    _run_ingest_service(published, 124)
    assert not published.exists(), 'the safety-timeout path retained the watched source'
    _refuse_publication(monkeypatch)
    now[0] += 61
    settled = worker.run_once()
    assert settled.state == 'failed' and settled.error_code == 'import_failed'
    assert _book(ingest) is None and not list(ingest.iterdir())
    assert calls == ['https://example.org/book.pdf']
    assert not (worker.staging_dir / job.id).exists()


def test_terminal_processor_failure_marker_fails_the_job_and_removes_the_source(fixture, monkeypatch):
    """Exit 3 retains the book, so only an explicit result can settle the job."""
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    published = _book(ingest)
    _run_ingest_service(published, 3)
    assert published.is_file(), 'exit 3 must retain the source until the worker settles it'
    _refuse_publication(monkeypatch)
    now[0] += 61
    settled = worker.run_once()
    assert settled.state == 'failed' and settled.error_code == 'import_failed'
    # The retained book has to go, or the ingest service keeps re-detecting it.
    assert not list(ingest.iterdir())
    assert not (worker.staging_dir / job.id).exists()


def test_import_still_running_is_left_alone_and_not_rehashed(fixture, monkeypatch):
    """A long conversion is normal; reconciling must not touch the publication."""
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    published, before = _book(ingest), _book(fixture[5]).read_bytes()
    _refuse_publication(monkeypatch)
    monkeypatch.setattr(w, 'digest', lambda *a, **k: pytest.fail('re-hashed a published source'))
    for _ in range(3):
        now[0] += 61
        assert worker.run_once().state == 'importing'
    assert _book(ingest) == published and published.read_bytes() == before
    assert calls == ['https://example.org/book.pdf']


def test_import_that_never_acknowledges_is_bounded_and_clears_its_publication(fixture, monkeypatch):
    """A processor that dies silently left the job polling forever."""
    repo, worker, job, now, calls, ingest = fixture
    worker.import_deadline_seconds = 300
    assert worker.run_once().state == 'importing'
    published = _book(ingest)
    sidecar = Path(str(published) + '.cwa.json')
    assert sidecar.exists()
    _refuse_publication(monkeypatch)
    now[0] += 61
    assert worker.run_once().state == 'importing', 'failed a job still inside its deadline'
    now[0] += 300
    settled = worker.run_once()
    assert settled.state == 'failed' and settled.error_code == 'import_failed'
    assert not published.exists() and not sidecar.exists()
    assert not (worker.staging_dir / job.id).exists()


def test_abandoned_import_cannot_be_acknowledged_afterwards(fixture, monkeypatch):
    """The receipt path stays open for a slow import, but not for a discarded one."""
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    manifest = json.loads(Path(str(_book(ingest)) + '.cwa.json').read_text())
    _book(ingest).unlink()
    _refuse_publication(monkeypatch)
    now[0] += 61
    assert worker.run_once().error_code == 'import_failed'
    with pytest.raises(s.NotFound):
        repo.publication_intent(job.id, manifest['publication_token'], manifest['staging_key'])


def test_failed_import_retries_by_downloading_again_not_by_resuming(fixture, monkeypatch):
    """Abandoning the publication removes the source, so resuming would fail."""
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    _book(ingest).unlink()
    _refuse_publication(monkeypatch)
    now[0] += 61
    assert worker.run_once().error_code == 'import_failed'
    monkeypatch.undo()
    repo.retry(1, job.id)
    assert repo.get_job(1, job.id).state == 'queued'
    assert worker.run_once().state == 'importing'
    assert len(calls) == 2 and _book(ingest) is not None


def test_importing_job_is_still_reconciled_after_its_catalog_is_switched_off(fixture, monkeypatch):
    """Disabling a catalog must not strand a published job with its files."""
    repo, worker, job, now, calls, ingest = fixture
    assert worker.run_once().state == 'importing'
    _book(ingest).unlink()
    repo.set_connection_enabled(job.connection_id, False)
    _refuse_publication(monkeypatch)
    now[0] += 61
    settled = worker.run_once()
    assert settled.state == 'failed' and settled.error_code == 'import_failed'
    assert not list(ingest.iterdir()) and not (worker.staging_dir / job.id).exists()


def test_cancelling_a_staged_job_does_not_leave_its_source_behind(fixture, monkeypatch):
    """A cancelled job is never claimed again, but it kept its downloaded file."""
    repo, worker, job, now, calls, ingest = fixture
    original = w.persist_capability
    def cancel_once_staged(path, token):
        original(path, token)
        repo.request_cancel(1, job.id)
    monkeypatch.setattr(w, 'persist_capability', cancel_once_staged)
    assert worker.run_once().state == 'cancelled'
    assert not list(ingest.iterdir())
    assert not (worker.staging_dir / job.id).exists(), 'cancelled job kept its staged source'


def test_passively_cancelled_job_releases_its_source_on_the_next_sweep(fixture, monkeypatch):
    """Cancellation outside a claim is terminal immediately and never claimed."""
    repo, worker, job, now, calls, ingest = fixture
    def pause_once_staged(*args, **kwargs):
        raise w.Paused()
    monkeypatch.setattr(w, 'persist_capability', pause_once_staged)
    assert worker.run_once().state == 'staged'
    staged = worker.staging_dir / job.id
    assert staged.is_dir(), 'fixture did not leave a staged source to clean up'
    now[0] += 61
    repo.request_cancel(1, job.id)
    assert repo.get_job(1, job.id).state == 'cancelled'
    worker.run_once()
    assert not staged.exists()


def test_failed_publication_keeps_its_source_so_a_retry_can_resume(fixture, monkeypatch):
    """The cleanup sweep must not break resuming a publication that proved itself."""
    repo, worker, job, now, calls, ingest = fixture
    def unavailable(*args, **kwargs):
        raise w.StagingError('ingest_directory_unavailable')
    monkeypatch.setattr(w, 'publish', unavailable)
    assert worker.run_once().error_code == 'ingest_directory_unavailable'
    assert (worker.staging_dir / job.id).is_dir(), 'dropped a source a retry still needs'
    monkeypatch.undo()
    repo.retry(1, job.id)
    assert repo.get_job(1, job.id).state == 'publishing'
    assert worker.run_once().state == 'importing'
    assert len(calls) == 1, 're-downloaded a source that was still staged'
