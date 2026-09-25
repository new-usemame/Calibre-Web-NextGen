"""Shutdown facts must distinguish owned cleanup from an unexplained death."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from cps.services.reflow import native_ipc as ipc

pytestmark = pytest.mark.unit


def document(tmp_path, delay=0, script=None):
    p = subprocess.Popen([sys.executable, '-u', '-c',
        script or f'import sys,time; print("ready"); sys.stdin.read(); time.sleep({delay})'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
    assert p.stdout.readline() == b'ready\n'
    d = ipc.NativeDocument.__new__(ipc.NativeDocument)
    rows = []; releases = []
    d.process=p; d.closed=False; d.drain=None; d.control=None
    d.selector=selectors.DefaultSelector(); d.root=str(tmp_path / 'scratch')
    Path(d.root).mkdir(); d.error_tail=b''; d.last_phase='build'; d.seq=4
    d.last_completed_phase='build'; d.last_completed_sequence=4
    d.ledger=SimpleNamespace(record=lambda value, **kw: rows.append(dict(value)))
    d.resource_lease=SimpleNamespace(close=lambda: releases.append(list(rows)))
    if script is not None: d.descendant_pid = int(p.stdout.readline())
    d.stderr_drain_state='pending'; d.stderr_drain_error=None
    d.drain=threading.Thread(target=d._drain_errors, daemon=True); d.drain.start()
    return d, rows, releases


@pytest.mark.parametrize('delay,expected_wait,code', [(0,'reaped',0), (3,'reaped_after_signal_request',-9)])
def test_eof_shutdown_records_owned_signal_and_reap_before_lease(tmp_path, delay, expected_wait, code):
    d, rows, releases = document(tmp_path, delay)
    try:
        d.close()
        result = next(x for x in rows if x['event']=='shutdown')
        assert result['entry_exit_code'] is None
        assert result['completed_phase']=='build' and result['completed_sequence']==4
        assert result['eof']=='closed' and result['wait']==expected_wait
        assert result['exit_code']==code and result['direct_child_reaped']
        assert result['group_state']=='absent'
        assert any(x['reason']=='eof_timeout' and x['result']=='sent' for x in result['signals']) == bool(delay)
        assert releases == [rows]
        if delay:
            with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
        else: d.require_clean_shutdown()
        assert not Path(d.root).exists()
        d.close(); assert len(releases)==1
    finally:
        if d.process.poll() is None: d.process.kill(); d.process.wait()


def test_preexisting_unexpected_death_is_not_owned_eof_escalation(tmp_path):
    d, rows, _ = document(tmp_path)
    os.kill(d.process.pid, signal.SIGKILL); d.process.wait()
    d.close()
    result = next(x for x in rows if x['event']=='shutdown')
    assert result['entry_exit_code']==-9 and result['exit_code']==-9
    assert result['wait']=='reaped'
    assert not any(x['reason']=='eof_timeout' for x in result['signals'])


def test_incomplete_group_evidence_is_not_reported_as_absent(tmp_path, monkeypatch):
    d, rows, releases = document(tmp_path)
    # A direct child may be reaped while a descendant or inaccessible group remains.
    monkeypatch.setattr(d, '_group_state', lambda pid: 'unknown')
    d.close()
    result = next(x for x in rows if x['event']=='shutdown')
    assert result['direct_child_reaped'] and result['group_state']=='unknown'
    assert releases == [rows]


def test_wait_error_still_records_incomplete_shutdown_before_lease(tmp_path, monkeypatch):
    d, rows, releases = document(tmp_path, 30)
    real_wait = d.process.wait
    monkeypatch.setattr(d.process, 'wait', lambda **kw: (_ for _ in ()).throw(RuntimeError('wait failed')))
    try:
        with pytest.raises(RuntimeError, match='wait failed'): d.close()
        result = next(x for x in rows if x['event']=='shutdown')
        assert result['wait']=='not_started' and not result['direct_child_reaped']
        assert result['group_state']=='present' and releases == [rows]
    finally:
        d.process.kill(); real_wait()


def test_descendant_cleanup_signal_is_distinct_from_direct_child_reap(tmp_path):
    script = """import os,sys,time
child = os.fork()
if child == 0:
    time.sleep(30)
else:
    print('ready', flush=True)
    print(child, flush=True)
    sys.stdin.read()
"""
    d, rows, _ = document(tmp_path, script=script)
    child = d.descendant_pid
    try:
        d.close()
        result = next(x for x in rows if x['event']=='shutdown')
        assert result['exit_code']==0 and result['direct_child_reaped']
        assert any(x['signal']=='SIGKILL' and x['reason']=='descendant_cleanup'
                   and x['result']=='sent' and x['group_state_before']=='present'
                   for x in result['signals'])
        # A killed orphan may briefly remain a zombie. Never translate group
        # presence into confirmed reap merely because the direct child exited.
        assert result['group_state'] in ('present', 'absent', 'unknown')
        if result['group_state'] != 'absent':
            with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    finally:
        try: os.kill(child, signal.SIGKILL)
        except ProcessLookupError: pass


@pytest.mark.parametrize('field,value', [('group_state','unknown'), ('exit_code',-9), ('entry_exit_code',0),
                                          ('completed_sequence',3), ('completed_phase','prepare')])
def test_clean_shutdown_gate_refuses_uncertain_or_signalled_terminal(tmp_path, field, value):
    d, _, _ = document(tmp_path)
    d.close(); d.require_clean_shutdown()
    d.shutdown_evidence[field] = value
    with pytest.raises(ipc.ChildExited, match='cleanup failed'): d.require_clean_shutdown()


from tests.unit.test_reflow_task import rig, _run, _ledger_rows
from cps.services.worker import STAT_FAIL


def test_published_artifact_retained_but_unknown_cleanup_is_not_task_success(rig, monkeypatch):
    real_close = ipc.NativeDocument.close
    def close_unknown(self):
        real_close(self)
        if self.last_phase == 'build': self.shutdown_evidence['group_state']='unknown'
    monkeypatch.setattr(ipc.NativeDocument, 'close', close_unknown)
    task = _run(rig, mode='full')
    assert task.stat == STAT_FAIL
    assert task.error == 'Published artifact retained; runtime cleanup failed'
    assert task.results['sha256']
    assert Path(task.results['path']).is_file()
    rows = _ledger_rows(rig)
    assert rows[0]['status']=='failed'
    journals = list(Path(rig.root).glob('jobs/*/*.jsonl'))
    import json
    events = [json.loads(line) for path in journals for line in path.read_text().splitlines()]
    assert any(x.get('kind')=='publication' and x.get('event')=='committed' for x in events)
    assert not any(x.get('kind')=='job' and x.get('status')=='done' for x in events)


def test_shutdown_journal_failure_cannot_satisfy_success_gate(tmp_path):
    d, _, _ = document(tmp_path)
    def fail_record(*args, **kwargs): raise OSError('journal unavailable')
    d.ledger = SimpleNamespace(record=fail_record)
    d.close()
    assert d.shutdown_evidence['exit_code']==0
    assert d.shutdown_evidence['record_error']=='OSError'
    with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()


def test_stopped_drain_on_read_error_fails_task_and_retains_publication(rig, monkeypatch):
    original = ipc.NativeDocument._drain_errors
    def broken_stream(self):
        stream = self.process.stdout
        class BrokenRead:
            def read(self, size): raise OSError('injected stderr read error')
            def close(self): return stream.close()
        self.process.stdout = BrokenRead()
        original(self)
    monkeypatch.setattr(ipc.NativeDocument, '_drain_errors', broken_stream)
    task = _run(rig, mode='full')
    assert task.stat == STAT_FAIL
    assert task.error == 'Published artifact retained; runtime cleanup failed'
    assert Path(task.results['path']).is_file() and task.results['sha256']
    assert _ledger_rows(rig)[0]['status']=='failed'
    import json
    rows=[json.loads(line) for path in Path(rig.root).glob('jobs/*/*.jsonl') for line in path.read_text().splitlines()]
    shutdown=next(x for x in rows if x.get('kind')=='native_shutdown')
    assert shutdown['stderr_drain_state']=='error'
    assert shutdown['stderr_drain_error']=='OSError'
    assert not shutdown['stderr_drain_complete']
    assert any(x.get('kind')=='publication' and x.get('event')=='committed' for x in rows)


def test_pending_alive_drain_cannot_satisfy_shutdown_gate(tmp_path):
    d, rows, _ = document(tmp_path)
    d.process.stdin.close(); d.process.wait(); d.drain.join(timeout=2)
    release = threading.Event()
    d.stderr_drain_state='pending'
    d.drain=threading.Thread(target=release.wait, daemon=True); d.drain.start()
    try:
        d.close()
        assert d.shutdown_evidence['stderr_drain_state']=='pending'
        assert not d.shutdown_evidence['stderr_drain_complete']
        assert d.drain.is_alive()
        with pytest.raises(ipc.ChildExited): d.require_clean_shutdown()
    finally:
        release.set(); d.drain.join(timeout=2)
