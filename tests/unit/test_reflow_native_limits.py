"""Procedural PDFs and supervised workers exercise bounded native work."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import zlib

import pymupdf
import pytest

from cps.services.reflow import extract, native_ipc as ipc
from cps.services.worker import STAT_FAIL
from tests.unit.test_reflow_task import rig, _run, _ledger_rows

pytestmark = pytest.mark.unit


def _stream_pdf(path, decoded, *, count=1):
    # Compress constant procedural bytes incrementally; never allocate the output.
    compressor = zlib.compressobj()
    compressed = bytearray()
    chunk = b' ' * 65536
    remaining = decoded
    while remaining:
        part = min(remaining, len(chunk))
        compressed.extend(compressor.compress(chunk[:part]))
        remaining -= part
    compressed.extend(compressor.flush())
    with pymupdf.open() as doc:
        page = doc.new_page()
        streams = []
        for _ in range(count):
            xref = doc.get_new_xref()
            doc.update_object(xref, '<<>>')
            doc.update_stream(xref, bytes(compressed), compress=False)
            doc.xref_set_key(xref, 'Filter', '/FlateDecode')
            streams.append(xref)
        doc.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{n} 0 R' for n in streams) + ']')
        doc.save(path)


def test_actual_worker_rejects_expansion_before_page_extraction(tmp_path):
    path = tmp_path / 'expanded.pdf'
    _stream_pdf(path, 128 * 1024**2 + 1)
    assert path.stat().st_size < 200000
    with pytest.raises(ValueError, match='decoded PDF stream exceeds'):
        with ipc.NativeDocument(path, scratch_root=tmp_path / 'scratch'):
            pytest.fail('oversized stream admitted')
    assert list((tmp_path / 'scratch').iterdir()) == []


def test_decoded_budget_counts_distinct_streams_and_allows_boundary(tmp_path, monkeypatch):
    from cps.services.reflow import native_limits
    path = tmp_path / 'bounded.pdf'
    _stream_pdf(path, 32, count=2)
    monkeypatch.setattr(native_limits, 'MAX_STREAM_DECODED_BYTES', 32)
    monkeypatch.setattr(native_limits, 'MAX_DOCUMENT_DECODED_BYTES', 64)
    with pymupdf.open(path) as doc:
        assert native_limits.check_document(doc) == 64
    monkeypatch.setattr(native_limits, 'MAX_DOCUMENT_DECODED_BYTES', 63)
    with pymupdf.open(path) as doc:
        with pytest.raises(ValueError, match='decoded PDF document exceeds'):
            native_limits.check_document(doc)


def test_password_error_survives_real_task_and_keeps_prior_epub(rig):
    path = rig.folder / 'Book - Author.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30, 40), 'Synthetic text written for this test.')
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw='owner-fixture', user_pw='reader-fixture')
    previous = rig.folder / 'Book - Author.epub'
    previous.write_bytes(b'previous complete output')
    task = _run(rig, mode='full')
    assert task.stat == STAT_FAIL
    assert 'password-protected' in task.error
    assert previous.read_bytes() == b'previous complete output'
    assert _ledger_rows(rig)[0]['status'] == 'failed'
    assert list((Path(rig.root) / 'native-scratch').iterdir()) == []


def test_empty_user_password_remains_readable(tmp_path):
    path = tmp_path / 'readable.pdf'
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw='owner-fixture', user_pw='')
    with ipc.NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        assert len(doc) == 1


def test_operation_deadline_kills_and_reaps_own_group_despite_progress(tmp_path, monkeypatch):
    # The substitute process follows the real control protocol, then stalls while
    # emitting progress. It also owns a child; an unrelated process must survive.
    child_pid = tmp_path / 'child.pid'
    worker = tmp_path / 'worker.py'
    worker.write_text('''import json,os,subprocess,sys,time
sys.path.insert(0, sys.argv[4])
from cps.services.reflow import native_ipc as ipc
control=os.fdopen(int(sys.argv[3]),'w',buffering=1)
for line in sys.stdin:
 seq=int(line)
 if seq==1:
  ipc.write_owned('.', 'response.json', dict(seq=seq,ok=True,value=1))
  control.write(json.dumps(dict(seq=seq,ready=True))+'\\n')
 else:
  child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'])
  with open(sys.argv[5],'w') as handle:handle.write(str(child.pid))
  end=time.monotonic()+1.2
  while time.monotonic()<end:
   control.write(json.dumps(dict(seq=seq,progress=dict(stage='waiting',page=0,pages=1)))+'\\n')
   sys.stdin.readline()
   time.sleep(.03)
  ipc.write_owned('.', 'response.json', dict(seq=seq,ok=True,value=1))
  control.write(json.dumps(dict(seq=seq,ready=True))+'\\n')
''')
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        doc.new_page(); doc.save(source)
    real_launch = ipc.subprocess.Popen
    def launch(arguments, **kwargs):
        if str(ipc.WORKER) in arguments:
            arguments = list(arguments) + [str(Path(ipc.__file__).parents[3]), str(child_pid)]
        return real_launch(arguments, **kwargs)
    monkeypatch.setattr(ipc, 'WORKER', worker)
    monkeypatch.setattr(ipc.subprocess, 'Popen', launch)
    unrelated = real_launch([sys.executable, '-c', 'import time; time.sleep(90)'])
    progress = []
    doc = None
    try:
        doc = ipc.NativeDocument(source, scratch_root=tmp_path / 'scratch')
        monkeypatch.setattr(ipc, 'operation_timeout', lambda *args: .35)
        started = time.monotonic()
        with pytest.raises(TimeoutError, match='native PDF operation exceeded'):
            doc.call('survey', {}, callback=progress.append)
        assert time.monotonic() - started < 5
        assert len(progress) >= 2
        assert doc.closed and doc.process.returncode == -signal.SIGKILL
        assert doc.shutdown_evidence['direct_child_reaped']
        assert doc.shutdown_evidence['signals'][0]['reason'] == 'operation_deadline'
        deadline = time.monotonic() + 3
        while doc._group_state(doc.process.pid) != 'absent' and time.monotonic() < deadline:
            time.sleep(.02)
        assert doc._group_state(doc.process.pid) == 'absent'
        assert unrelated.poll() is None
        assert list((tmp_path / 'scratch').iterdir()) == []
    finally:
        if doc is not None: doc.close()
        unrelated.kill(); unrelated.wait(timeout=5)


def test_bulk_timeout_is_hard_bounded_and_accounts_for_selected_pages():
    assert ipc.operation_timeout('open', {}, 2000) == 60
    assert ipc.operation_timeout('prepare', {'page_numbers': [4, 9]}, 2000) == 1920
    assert ipc.operation_timeout('prepare', {}, 2000) == 21600
    assert ipc.operation_timeout('build', {'page_html': {1: '', 2: ''}}, 2000) == 1920


def test_task_deadline_preserves_prior_artifact_and_ambiguous_billing(rig, monkeypatch):
    from types import SimpleNamespace
    previous = rig.folder / 'Book - Author.epub'
    previous.write_bytes(b'previous complete output')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    original_call = ipc.NativeDocument.call
    original_timeout = ipc.operation_timeout
    owned = []
    def stall(self, operation, arguments, **kwargs):
        if operation in ('prepare', 'vision_convert'):
            self.ledger.reserve_attempt('unresolved-fixture', .01)
            owned.append(self.process)
            os.kill(self.process.pid, signal.SIGSTOP)
        return original_call(self, operation, arguments, **kwargs)
    monkeypatch.setattr(ipc.NativeDocument, 'call', stall)
    monkeypatch.setattr(ipc, 'operation_timeout', lambda operation, args, pages:
                        .2 if operation in ('prepare', 'vision_convert') else original_timeout(operation, args, pages))
    task = _run(rig, mode='full', replace_existing_epub=True)
    assert task.stat == STAT_FAIL and 'safety deadline' in task.error
    assert owned and all(process.returncode == -signal.SIGKILL for process in owned)
    row = _ledger_rows(rig)[0]
    assert row['status'] == 'failed' and row['pending_usd'] == pytest.approx(.01)
    assert previous.read_bytes() == b'previous complete output'
    assert rig.local_db.session.commits == 0
    assert list((Path(rig.root) / 'native-scratch').iterdir()) == []
