"""Real task integration: native failure cannot take the web process with it."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import zipfile
from types import SimpleNamespace
import pymupdf
import pytest
from cps.services.reflow import native_ipc as ipc, native_codec as codec
from cps.services.reflow import build_epub, structural_pipeline, source, extract, skeleton
from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS, STAT_ENDED
from tests.unit.test_reflow_task import rig, _run, _ledger_rows
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


def test_native_death_during_actual_task_fails_job_keeps_web_alive_and_prior_file(rig, monkeypatch):
    """Kill the actual worker after admission, before publication; a missing
    boundary or a swallowed failure cannot satisfy these persisted-state checks."""
    original = rig.folder / 'Book - Author.epub'; original.write_bytes(b'prior reader artifact')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    real = ipc.NativeDocument.call; seen = []
    def die(self, operation, args, **kwargs):
        if operation == 'build':
            seen.append(self.process.pid); os.kill(self.process.pid, signal.SIGKILL)
        return real(self, operation, args, **kwargs)
    monkeypatch.setattr(ipc.NativeDocument, 'call', die)
    parent = os.getpid()
    task = _run(rig, mode='full', replace_existing_epub=True)
    assert seen and parent == os.getpid()
    assert task.stat == STAT_FAIL and 'worker' in task.error.lower()
    assert _ledger_rows(rig)[0]['status'] == 'failed'
    assert original.read_bytes() == b'prior reader artifact'
    assert rig.local_db.session.commits == 0
    assert not list(rig.folder.glob('.reflow-*-staging'))
    assert not list((Path(rig.root) / 'native-scratch').iterdir())
    from cps import app, web
    # This lightweight unit process has not registered the complete web module.
    # Restore Flask's test-only initialization state for later API fixtures.
    with monkeypatch.context() as context:
        context.setattr(app, '_got_first_request', app._got_first_request)
        # Earlier API tests may have registered real web response hooks, while
        # this unit rig deliberately never initializes the application config DB.
        # Initialize their small config surface, not an order-dependent 500.
        context.setattr(web, 'config', SimpleNamespace(config_trustedhosts='', config_use_google_drive=False))
        with app.test_client() as client:
            assert client.get('/__native-containment-alive__').status_code == 404
    monkeypatch.setattr(ipc.NativeDocument, 'call', real)
    second = _run(rig, mode='sample', sample_pages=1)
    assert second.stat == STAT_FINISH_SUCCESS, second.error


def test_task_cancel_interrupts_blocked_child_only_and_cleans_owned_files(rig, monkeypatch):
    task = rig.mod.TaskReflowPdf(5, 7, {'mode': 'full'})
    real = ipc.NativeDocument.call
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    owned = []
    def pause(self, operation, args, **kwargs):
        if operation == 'prepare':
            owned.append(self.process); os.kill(self.process.pid, signal.SIGSTOP)
            task.stat = STAT_ENDED
        return real(self, operation, args, **kwargs)
    monkeypatch.setattr(ipc.NativeDocument, 'call', pause)
    try:
        task.run(None)
        assert task.stat == STAT_ENDED
        assert owned and owned[0].poll() is not None
        assert unrelated.poll() is None
        assert _ledger_rows(rig)[0]['status'] == 'cancelled'
        assert not list((Path(rig.root) / 'native-scratch').iterdir())
        assert not (rig.folder / 'Book - Author.epub').exists()
    finally:
        unrelated.terminate(); unrelated.wait(timeout=5)


def test_json_codec_preserves_source10_fields_and_atomic_glyphs():
    span = extract.Span(text='1st', bbox=(1, 2, 3, 4), size=12, font='Times', flags=0)
    span.transcription_uncertain = True
    style = skeleton.BookStyle(body_size=12); style.folio_boxes = {7: (1., 2., 3., 4.)}
    recovery = source.PageRecovery(pno=7); recovery.verification = {'status': 'uncertain', 'OCR_alternate': 'NEVER SUBSTITUTE'}
    glyph = ['glyph', '1st', {'reason': 'transcript', 'bbox': (1., 2., 3., 4.)}]
    line = extract.Line([span], (1., 2., 3., 4.)); line.transcription_uncertain = True
    raw = extract.RawPage(7, 100., 200.); raw.transcript_unverified = True
    restored = codec.loads(codec.dumps([span, style, recovery, glyph, line, raw]))
    assert restored[0].transcription_uncertain is True
    assert restored[1].folio_boxes == style.folio_boxes
    assert restored[2].verification == recovery.verification
    assert restored[3] == glyph and restored[0].text == '1st'
    assert restored[4].transcription_uncertain is True
    assert restored[4].spans[0].transcription_uncertain is True
    assert restored[5].transcript_unverified is True
    span.transcription_uncertain = 'false'
    with pytest.raises(ValueError, match='certainty'): codec.loads(codec.dumps(span))


@pytest.mark.parametrize('raw', [b'{"version":1,"version":1,"value":null}',
    b'{"version":999,"value":null}', b'{"version":1,"value":{"type":"os.system","value":{}}}',
    b'{"version":1,"value":NaN}'])
def test_unknown_or_ambiguous_wire_schema_rejects(raw):
    with pytest.raises((ValueError, TypeError)): codec.loads(raw)


def test_owned_manifest_rejects_symlink_and_arbitrary_path(tmp_path):
    outside = tmp_path / 'outside'; outside.write_bytes(b'private')
    owned = tmp_path / 'owned'; owned.mkdir()
    (owned / 'response.json').symlink_to(outside)
    with pytest.raises(OSError): ipc.read_owned(owned, 'response.json')
    with pytest.raises(ValueError): ipc.read_owned(owned, '../outside')
    with pytest.raises(ValueError): ipc.write_owned(owned, '/outside', {})


def test_real_child_and_direct_pipeline_emit_same_semantic_epub(tmp_path):
    path = tmp_path / 'source.pdf'
    with F.new_doc() as doc:
        F.chapter_opening_page(doc, 'A complete chapter'); F.prose_page(doc); doc.save(path)
    def make(doc, destination):
        result = structural_pipeline.run_structural(doc, recovery_opts={'mode': 'off'})
        built = build_epub.build(result.book, str(destination), doc=doc,
            source_pages=result.source_pages, page_html=result.page_html,
            operation_plans=result.operation_plans, identifier='urn:test:semantic')
        assert build_epub.validate(built.path) == []
        return result
    with pymupdf.open(path) as doc: direct = make(doc, tmp_path / 'direct.epub')
    with ipc.NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        remote = make(doc, tmp_path / 'remote.epub')
    assert direct.structural == remote.structural
    assert direct.book.conservation.legacy_lexical
    assert remote.book.conservation.to_dict() == direct.book.conservation.to_dict()
    assert codec.dumps(direct.book) == codec.dumps(remote.book)
    with zipfile.ZipFile(tmp_path / 'direct.epub') as a, zipfile.ZipFile(tmp_path / 'remote.epub') as b:
        assert set(a.namelist()) == set(b.namelist())
        for name in a.namelist():
            if name not in ('META-INF/reflow.json', 'OEBPS/content.opf'):
                assert a.read(name) == b.read(name), name


def test_child_environment_excludes_secrets_and_uses_installed_script(rig, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'must-never-inherit')
    monkeypatch.setenv('PYTHONPATH', '/untrusted-config')
    real = ipc.subprocess.Popen; launches = []
    def launch(*args, **kwargs):
        launches.append((args, kwargs)); return real(*args, **kwargs)
    monkeypatch.setattr(ipc.subprocess, 'Popen', launch)
    task = _run(rig, mode='sample', sample_pages=1)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    native = [(a, kw) for a, kw in launches if str(ipc.WORKER) in a[0]]
    assert native
    for args, kw in native:
        assert '-I' in args[0] and kw['start_new_session']
        assert 'OPENROUTER_API_KEY' not in kw['env'] and 'PYTHONPATH' not in kw['env']


@pytest.mark.parametrize('stop_kind', ['death', 'cancel', 'capacity'])
def test_parent_typed_billing_survives_native_death_without_paid_replay(rig, monkeypatch, stop_kind):
    """Real task + native child + typed provider/ledger/cache; only HTTP is fake.
    Crash after paid adoption, then retry: neither charge nor approved decision
    can disappear or be replayed, and nothing publishes from the failed child."""
    import json
    from cps.services.reflow import model, ledger, layout_pipeline
    from tests.unit.test_reflow_task import _LayoutSession
    with F.new_doc() as pdf:
        page = pdf.new_page(width=500, height=700)
        page.insert_text((80, 100), '"Original displayed words remain exactly as printed."', fontsize=12)
        for y in (180, 195, 210):
            page.insert_text((50, y), 'Ordinary body context supports the source display.', fontsize=12)
        pdf.save(rig.folder / 'Book - Author.pdf')
    provider = _LayoutSession(); calls = provider.calls; parent = os.getpid()
    def answer(*args, **kwargs):
        assert os.getpid() == parent, 'provider dispatch escaped parent'
        return provider.post(*args, **kwargs)
    monkeypatch.setattr(layout_pipeline, 'QUALITY_RELEASED', True)
    monkeypatch.setattr(rig.mod.config, 'resolved_openrouter_key', lambda: 'inert-parent-key')
    monkeypatch.setattr(model.requests, 'get', provider.get)
    monkeypatch.setattr(model.requests, 'post', answer)
    previous = rig.folder / 'Book - Author.epub'; previous.write_bytes(b'previous reader artifact')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    failed = rig.mod.TaskReflowPdf(5, 7, {'mode': 'full', 'cost_cap_usd': 1,
        'review_mode': 'source_verified', 'replace_existing_epub': True})
    real = ipc.NativeDocument.call
    def die(self, operation, args, **kwargs):
        if operation == 'build':
            assert len(calls) == 2
            assert self.ledger.pending_usd() == 0
            if stop_kind == 'death': os.kill(self.process.pid, signal.SIGKILL)
            elif stop_kind == 'capacity':
                from cps.services.reflow import native_resources
                monkeypatch.setattr(native_resources, 'measure', lambda root: (0, 8 * 1024**3))
                self.resource_lease.next_check = 0
            else: failed.stat = STAT_ENDED
        return real(self, operation, args, **kwargs)
    monkeypatch.setattr(ipc.NativeDocument, 'call', die)
    failed.run(None)
    assert failed.stat == (STAT_FAIL if stop_kind == 'death' else STAT_ENDED), failed.error
    assert previous.read_bytes() == b'previous reader artifact'
    row = next(r for r in _ledger_rows(rig) if r['job_id'] == failed.job_id)
    assert row['status'] == ('failed' if stop_kind == 'death' else 'cancelled') and row['pending_usd'] == 0
    assert row['spend_usd'] == pytest.approx(2 * .000123456789)
    audit = ledger.Ledger(str(Path(rig.root) / 'jobs' / '5' / (failed.job_id + '.jsonl')), 1)
    assert [e['event'] for e in audit.entries('operation_audit')] == ['validated']
    exits = audit.entries('native_worker')
    assert len(exits) == 1
    assert exits[0]['exit_code'] == -signal.SIGKILL
    if stop_kind != 'death':
        shutdown = audit.entries('native_shutdown')
        assert len(shutdown) == 1
        stopped = shutdown[0]
        reason = 'cancelled' if stop_kind == 'cancel' else 'resource_stop'
        assert stopped['signals'][0]['reason'] == reason
        assert stopped['signals'][0]['signal'] == 'SIGKILL'
        assert stopped['signals'][0]['result'] == 'sent'
        assert stopped['wait'] == 'reaped_after_signal_request'
        assert stopped['direct_child_reaped'] and stopped['group_state'] == 'absent'
        assert stopped['stderr_drain_complete']
        assert stopped['stderr_drain_state'] == 'eof'
        assert stopped['stderr_drain_error'] is None
        if stop_kind == 'capacity':
            assert stopped['resource_stop_error'] == 'ResourceStopped'
    monkeypatch.setattr(ipc.NativeDocument, 'call', real)
    if stop_kind == 'capacity':
        from cps.services.reflow import native_resources
        monkeypatch.setattr(native_resources, 'measure', lambda root: (8 * 1024**3, 8 * 1024**3))
    success = _run(rig, mode='full', cost_cap_usd=1, replace_existing_epub=True)
    assert success.stat == STAT_FINISH_SUCCESS, success.error
    assert len(calls) == 2, 'durably answered requests must be cache hits on retry'
    assert success.results['report']['spend']['usd'] == 0
    assert success.results['report']['structural']['approved_pages'] == 1
    assert success.results['report']['structural']['approved_operations'] > 0
    assert build_epub.validate(success.results['path']) == []


def test_task_and_http_assessment_never_open_native_pdf_in_parent(rig, monkeypatch):
    from cps.api import reflow as api
    from concurrent.futures import ThreadPoolExecutor
    def forbidden(*args, **kwargs):
        raise AssertionError('native PDF open in application process')
    monkeypatch.setattr(pymupdf, 'open', forbidden)
    monkeypatch.setattr(api, 'REFLOW_DIR', rig.root)
    path = rig.folder / 'Book - Author.pdf'
    assert api._page_count_uncached(str(path)) > 0
    assert api._survey_uncached(str(path))['pages'] > 0
    # The actual service invokes conversion from a worker thread, not main.
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(_run, rig, mode='sample', sample_pages=1).result()
    assert task.stat == STAT_FINISH_SUCCESS, task.error


def test_native_rejects_external_request_paths_and_changed_snapshot(rig, tmp_path):
    source_path = rig.folder / 'Book - Author.pdf'
    with ipc.NativeDocument(source_path, scratch_root=tmp_path / 'scratch') as doc:
        with pytest.raises(ValueError):
            doc.call('prepare', {'recovery_opts': {'cache_dir': '/external'}})
        with pytest.raises(ValueError):
            doc.call('build', {'out_path': '/external.epub'})
        (Path(doc.root) / 'source.pdf').write_bytes(b'changed')
        with pytest.raises(ValueError): doc.call('survey', {})


def test_wire_size_depth_and_unregistered_fields_are_rejected(monkeypatch):
    monkeypatch.setattr(codec, 'MAX_BYTES', 64)
    with pytest.raises(ValueError, match='size'): codec.dumps('x' * 100)
    with pytest.raises(ValueError, match='size'): codec.loads(b' ' * 100)
    monkeypatch.setattr(codec, 'MAX_DEPTH', 3)
    with pytest.raises(ValueError, match='nesting'): codec.dumps([[[[[]]]]])
    span = extract.Span(text='safe', bbox=(1, 2, 3, 4), size=12, font='Times', flags=0)
    span.executable = 'must not cross'
    with pytest.raises(ValueError, match='fields'): codec.dumps(span)


@pytest.mark.parametrize('fault', ['manifest_path', 'symlink', 'nonboolean_ok'])
def test_actual_task_rejects_untrusted_completed_child_artifacts(rig, monkeypatch, tmp_path, fault):
    previous = rig.folder / 'Book - Author.epub'; previous.write_bytes(b'prior EPUB')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    outside = tmp_path / 'outside.epub'; outside.write_bytes(b'unrelated private bytes')
    real_call = ipc.NativeDocument.call
    real_read = ipc.read_owned
    def read(root, name):
        raw = real_read(root, name)
        if fault == 'nonboolean_ok' and name == 'response.json':
            result = codec.loads(raw)
            if isinstance(result.get('value'), build_epub.BuildResult):
                result['ok'] = 'true'; return codec.dumps(result)
        return raw
    def tamper(self, operation, args, **kwargs):
        result = real_call(self, operation, args, **kwargs)
        if operation == 'build':
            if fault == 'manifest_path': result.path = str(outside)
            elif fault == 'symlink':
                candidate = Path(self.root) / 'candidate.epub'
                candidate.unlink(); candidate.symlink_to(outside)
        return result
    monkeypatch.setattr(ipc, 'read_owned', read)
    monkeypatch.setattr(ipc.NativeDocument, 'call', tamper)
    task = _run(rig, mode='full', replace_existing_epub=True)
    assert task.stat == STAT_FAIL, task.error
    assert previous.read_bytes() == b'prior EPUB'
    assert outside.read_bytes() == b'unrelated private bytes'
    assert rig.local_db.session.commits == 0
    assert not list((Path(rig.root) / 'native-scratch').iterdir())


def test_native_diagnostics_keep_only_bounded_tail_without_touching_protocol():
    import io
    doc = ipc.NativeDocument.__new__(ipc.NativeDocument)
    doc.error_tail = b''
    doc.process = SimpleNamespace(stdout=io.BytesIO(b'noise' * 20000 + b'last native fact'))
    doc._drain_errors()
    assert len(doc.error_tail) == 65536
    assert doc.error_tail.endswith(b'last native fact')


@pytest.mark.timeout(5)
def test_owned_manifest_fifo_rejects_without_waiting_for_a_writer(tmp_path):
    os.mkfifo(tmp_path / 'response.json')
    with pytest.raises(ValueError, match='regular'): ipc.read_owned(tmp_path, 'response.json')


def test_actual_native_build_forwards_source_evidence_progress(tmp_path, monkeypatch):
    from tests.unit.test_reflow_build_epub import _scanned_book
    scan, result = _scanned_book(monkeypatch, 2)
    path = tmp_path / 'scan.pdf'
    try: scan.save(path)
    finally: scan.close()
    progress = []; phases = []
    with ipc.NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        built = build_epub.build(result.book, str(tmp_path / 'scan.epub'), doc=doc,
            page_html=result.page_html, source_pages=result.source_pages,
            figure_transform=result.recovery.figure_rect,
            evidence_progress=lambda done, total: progress.append((done, total)),
            runtime_progress=lambda row: phases.append(row))
    assert progress == [(0, 2), (1, 2), (1, 2), (2, 2)]
    assert any(row.get('phase') == 'archive_finalize' for row in phases)
    assert len(built.sidecar['source_evidence']) == 2
    assert build_epub.validate(built.path) == []


def test_parent_rejects_forged_source_authority_before_native_preparation(rig, tmp_path):
    import json
    from dataclasses import replace
    from cps.services.reflow import structural_ops, typed_model
    with ipc.NativeDocument(rig.folder / 'Book - Author.pdf', scratch_root=tmp_path / 'scratch') as doc:
        result = structural_pipeline.run_structural(doc, recovery_opts={'mode': 'off'}, measure_eligibility=False)
        pno, canonical = next(iter(result.source_pages.items()))
        forged = replace(canonical, html=canonical.html + '<p>invented</p>')
        sequence = doc.seq
        with pytest.raises(structural_ops.ContractError):
            structural_ops.prepare(result.book, doc, pno, typed_model.SOURCE_REVISION,
                json.loads(canonical.provenance_json), source_page=forged)
        assert doc.seq == sequence, 'parent must not send a forged source for native or paid work'


def test_native_recovery_cache_miss_has_owned_scratch_and_keeps_failure_pixels(tmp_path, monkeypatch):
    from tests.unit.test_reflow_ocr_adapter import _scan
    executable = tmp_path / 'tesseract'
    executable.write_text('#!/bin/sh\ncase "$1" in\n'
        ' --version) echo "tesseract diagnostic-no-recognition";;\n'
        ' --list-langs) printf "List of available languages in \\\"%s\\\" (2):\\neng\\nosd\\n" "$TESSDATA_PREFIX";;\n'
        ' *) exit 97;;\nesac\n')
    executable.chmod(0o700)
    (tmp_path / 'eng.traineddata').write_bytes(b'engine identity fixture')
    (tmp_path / 'osd.traineddata').write_bytes(b'orientation identity fixture')
    monkeypatch.setenv('TESSDATA_PREFIX', str(tmp_path))
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    path = tmp_path / 'scan.pdf'
    with _scan() as doc: doc.save(path)
    with ipc.NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        result = structural_pipeline.run_structural(doc, recovery_opts={'mode': 'auto'}, measure_eligibility=False)
        assert result.recovery.failed == 1
        assert result.recovery.provenance[0].failed
        assert result.book.figures, 'failed recognition must retain the complete scanned source'
    assert not list((tmp_path / 'scratch').iterdir())
