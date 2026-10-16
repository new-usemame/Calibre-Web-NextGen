"""Post-command child diagnostics locate slow close without forgiving it."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from cps.services.reflow import native_ipc as ipc
from tests.fixtures import reflow_pdfs as F


def test_actual_native_eof_records_phases_without_stderr_or_protocol_noise(tmp_path):
    source = tmp_path / 'input.pdf'
    doc = F.new_doc(); F.prose_page(doc); doc.save(source); doc.close()
    d = ipc.NativeDocument(source, scratch_root=tmp_path/'scratch', cache_root=tmp_path/'state')
    assert len(d) == 1
    d.call('survey', {})  # Another normal reply must not consume a shutdown frame.
    d.close(); d.require_clean_shutdown()
    rows = d.shutdown_evidence['child_shutdown_phases']
    assert [r['phase'] for r in rows] == ['stdin_eof', 'document_close_started', 'document_closed', 'runtime_returning']
    assert all(r['seq'] == d.seq for r in rows)
    assert [r['monotonic_ns'] for r in rows] == sorted(r['monotonic_ns'] for r in rows)
    assert [r['cpu_ns'] for r in rows] == sorted(r['cpu_ns'] for r in rows)
    assert not d.error_tail
    assert not Path(d.root).exists()
    assert d.shutdown_evidence['group_state'] == 'absent'


def inert_closing_child(tmp_path, mode):
    readfd, writefd = os.pipe()
    root = str(Path(ipc.__file__).parent)
    script = '''import atexit,importlib,importlib.util,os,sys,time
from pathlib import Path
root=Path(sys.argv[1]);name='_isolated_close_test'
spec=importlib.util.spec_from_file_location(name,root/'__init__.py',submodule_search_locations=[str(root)])
m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
runtime=importlib.import_module(name+'.native_worker_runtime')
mode=sys.argv[3]
def slow_atexit(): time.sleep(5)
if mode=='atexit': atexit.register(slow_atexit)
class Document:
 def close(self):
  if mode=='document': time.sleep(5)
  if mode=='document_finishes': time.sleep(3)
  if mode=='document_failure': raise RuntimeError('close failed')
control=os.fdopen(int(sys.argv[2]),'w',buffering=1)
print('ready',flush=True)
sys.stdin.read()
runtime._shutdown_document(Document(),control,7,'stdin_eof')
control.close()
'''
    p = subprocess.Popen([sys.executable,'-I','-c',script,root,str(writefd),mode],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        start_new_session=True,pass_fds=(writefd,))
    os.close(writefd)
    assert p.stdout.readline() == b'ready\n'
    d = ipc.NativeDocument.__new__(ipc.NativeDocument)
    d.process=p;d.closed=False;d.control=os.fdopen(readfd,'rb',buffering=0)
    d.selector=selectors.DefaultSelector();d.buffer=b'';d.root=str(tmp_path/'scratch');Path(d.root).mkdir()
    d.error_tail=b'';d.last_phase=d.last_completed_phase='build';d.seq=d.last_completed_sequence=7
    d.ledger=None;d.resource_lease=None;d.stderr_drain_state='pending';d.stderr_drain_error=None
    d.drain=threading.Thread(target=d._drain_errors,daemon=True);d.drain.start()
    return d


def test_completed_build_waits_for_a_slow_but_finite_document_close(tmp_path):
    d = inert_closing_child(tmp_path, 'document_finishes')
    try:
        d.close()
        d.require_clean_shutdown()
        evidence = d.shutdown_evidence
        assert evidence['exit_code'] == 0
        assert evidence['wait'] == 'reaped'
        assert [row['phase'] for row in evidence['child_shutdown_phases']] == [
            'stdin_eof', 'document_close_started', 'document_closed', 'runtime_returning']
        assert evidence['document_close_grace']['trigger'] == 'document_close_started'
        assert not any(row['reason'].endswith('timeout') for row in evidence['signals'])
    finally:
        if d.process.poll() is None: d.process.kill(); d.process.wait()


@pytest.mark.parametrize('mode,last_phase,frame',[('document','document_close_started',b'in close'),('atexit','runtime_returning',b'in slow_atexit')])
def test_slow_exit_stacks_distinguish_document_from_interpreter_cleanup(tmp_path,mode,last_phase,frame,monkeypatch):
    if mode == 'document':
        monkeypatch.setattr(ipc, 'BUILD_DOCUMENT_CLOSE_GRACE_SECONDS', 1.2)
    d=inert_closing_child(tmp_path,mode)
    try:
        d.close()
        with pytest.raises(ipc.ChildExited):d.require_clean_shutdown()
        e=d.shutdown_evidence
        assert e['exit_code']==-9 and e['wait']=='reaped_after_signal_request'
        assert e['child_shutdown_phases'][-1]['phase']==last_phase
        assert d.error_tail.count(b'Timeout (')==1
        assert frame in d.error_tail
        assert e['stderr_drain_complete'] and e['group_state']=='absent'
        reason = 'document_close_timeout' if mode == 'document' else 'eof_timeout'
        assert any(s['reason']==reason for s in e['signals'])
    finally:
        if d.process.poll() is None:d.process.kill();d.process.wait()


def test_failed_document_close_is_not_accepted_as_cleanup(tmp_path):
    d = inert_closing_child(tmp_path, 'document_failure')
    d.close()
    with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    assert d.shutdown_evidence['exit_code'] != 0
    assert d.shutdown_evidence['child_shutdown_phases'][-1]['phase'] == 'document_close_started'
    assert d.shutdown_evidence['direct_child_reaped']


def test_uncompleted_build_does_not_receive_document_close_grace(tmp_path):
    d = inert_closing_child(tmp_path, 'document_finishes')
    d.last_completed_phase = 'prepare'
    d.close()
    with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    assert 'document_close_grace' not in d.shutdown_evidence
    assert any(s['reason'] == 'eof_timeout' for s in d.shutdown_evidence['signals'])


def test_cancellation_reaps_a_slow_close_without_using_grace_deadline(tmp_path):
    import time
    d = inert_closing_child(tmp_path, 'document')
    start = time.monotonic()
    d.should_stop = lambda: time.monotonic() - start > .35
    d.close()
    with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    assert time.monotonic() - start < 2
    assert any(s['reason'] == 'cancelled' for s in d.shutdown_evidence['signals'])
    assert d.shutdown_evidence['direct_child_reaped']


def test_shutdown_diagnostics_refuse_unbounded_or_mismatched_frames(tmp_path):
    d=ipc.NativeDocument.__new__(ipc.NativeDocument);d.seq=7;d.buffer=b''
    for payload in [b'x'*4097, json.dumps({'seq':8,'shutdown_phase':'stdin_eof','monotonic_ns':1,'cpu_ns':1}).encode()+b'\n']:
        readfd,writefd=os.pipe();os.write(writefd,payload);os.close(writefd)
        d.control=os.fdopen(readfd,'rb',buffering=0)
        try:
            rows,error=d._read_shutdown_phases()
            assert rows==[] and error
        finally:d.control.close()


def closing_resource_lease(d, tmp_path, monkeypatch):
    from cps.services.reflow import native_resources as resources
    with monkeypatch.context() as admission:
        admission.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
        lease = resources.Lease(tmp_path/'lease')
    lease.mode = 'checked'
    d.resource_lease = lease
    released = []
    close = lease.close

    def release_after_cleanup():
        # Resource-stop must not unwind out of close before owned work is reaped.
        assert d.process.poll() is not None
        assert d.shutdown_evidence['direct_child_reaped']
        assert d.shutdown_evidence['group_state'] == 'absent'
        assert d.shutdown_evidence['stderr_drain_complete']
        released.append(True)
        close()

    monkeypatch.setattr(lease, 'close', release_after_cleanup)
    return resources, lease, released


@pytest.mark.parametrize('capacity', ['adequate', 'low', 'unknown', 'measurement_error'])
def test_close_keeps_real_lease_reserve_checkpoint_until_owned_cleanup(
        tmp_path, monkeypatch, capacity):
    import time
    d = inert_closing_child(tmp_path, 'document_finishes')
    resources, lease, released = closing_resource_lease(d, tmp_path, monkeypatch)
    samples = []
    start = time.monotonic()

    def measure(_):
        samples.append(time.monotonic())
        # Exercise the extended wait, not just a pre-close admission sample.
        if time.monotonic() - start > 2.1:
            if capacity == 'measurement_error': raise OSError('fixture measurement')
            if capacity != 'adequate':
                return (None if capacity == 'unknown' else 0), 8*1024**3
        return 8*1024**3, 8*1024**3

    monkeypatch.setattr(resources, 'measure', measure)
    try:
        d.close()
        assert len(samples) >= 5
        assert released == [True] and lease.fd is None
        assert 'document_close_grace' in d.shutdown_evidence
        if capacity == 'adequate':
            d.require_clean_shutdown()
            assert d.shutdown_evidence['exit_code'] == 0
        else:
            with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
            assert any(s['reason'] == 'resource_stop' for s in d.shutdown_evidence['signals'])
            assert d.shutdown_evidence['resource_stop_error'] == (
                'OSError' if capacity == 'measurement_error' else 'ResourceStopped')
            assert d.shutdown_evidence['wait'] == 'reaped_after_signal_request'
    finally:
        if d.process.poll() is None: d.process.kill(); d.process.wait()
        if lease.fd is not None: os.close(lease.fd); lease.fd = None


def test_resource_stop_stays_failure_when_child_exits_before_signal(tmp_path, monkeypatch):
    d = inert_closing_child(tmp_path, 'document_finishes')
    resources, lease, released = closing_resource_lease(d, tmp_path, monkeypatch)
    samples = []

    def measure(_):
        samples.append(True)
        # Force the actual exit race between the wait-loop poll and stop result.
        d.process.wait(timeout=5)
        return 0, 8*1024**3

    monkeypatch.setattr(resources, 'measure', measure)
    try:
        d.close()
        assert samples and released == [True] and lease.fd is None
        e = d.shutdown_evidence
        assert e['exit_code'] == 0 and e['stderr_drain_complete']
        assert any(s['reason'] == 'resource_stop' and s['result'] == 'absent'
                   for s in e['signals'])
        with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    finally:
        if d.process.poll() is None: d.process.kill(); d.process.wait()
        if lease.fd is not None: os.close(lease.fd); lease.fd = None
