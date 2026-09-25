"""Capacity signals, real flock lifetime and actual Task launch/stop semantics."""
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from cps.services.reflow import native_resources as resources, native_ipc as ipc, quote_preparation
from cps.services.worker import STAT_FAIL, STAT_ENDED, STAT_FINISH_SUCCESS
from tests.unit.test_reflow_task import rig, _run, _ledger_rows
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('reading', [(0, 8 * 1024**3), (None, None), (8 * 1024**3, 0)])
def test_insufficient_or_unknown_capacity_launches_no_task_child(rig, monkeypatch, reading):
    monkeypatch.setattr(resources, 'measure', lambda root: reading)
    launched = []
    monkeypatch.setattr(ipc.subprocess, 'Popen', lambda *a, **k: launched.append(a))
    task = _run(rig, mode='full')
    assert task.stat == STAT_FAIL and 'capacity' in task.error.lower()
    assert launched == [] and rig.local_db.session.commits == 0
    assert _ledger_rows(rig)[0]['status'] == 'failed'
    # Admission failure closes the OS lease; no stale PID file blocks recovery.
    monkeypatch.setattr(resources, 'measure', lambda root: (8 * 1024**3, 8 * 1024**3))
    with resources.Lease(rig.root):
        pass


def test_actual_conversion_preparation_and_survey_share_one_lease(rig, monkeypatch):
    from cps.api import reflow as api
    monkeypatch.setattr(api, 'REFLOW_DIR', rig.root)
    pdf = rig.folder / 'Book - Author.pdf'
    with ipc.NativeDocument(pdf, scratch_root=Path(rig.root) / 'native-scratch', cache_root=rig.root):
        launched = []
        with monkeypatch.context() as patch:
            patch.setattr(ipc.subprocess, 'Popen', lambda *a, **k: launched.append(a))
            with pytest.raises(resources.ResourceUnavailable, match='busy'):
                api._survey_uncached(pdf)
            with pytest.raises(resources.ResourceUnavailable, match='busy'):
                quote_preparation.measure_isolated(pdf, {}, None, lambda: False, rig.root)
        assert launched == []
    # A completed child releases the shared boundary and the actual Task works.
    task = _run(rig, mode='sample', sample_pages=1)
    assert task.stat == STAT_FINISH_SUCCESS, task.error


def test_capacity_drop_cancels_owned_task_keeps_prior_file_and_unknown_hold(rig, monkeypatch):
    prior = rig.folder / 'Book - Author.epub'
    prior.write_bytes(b'prior')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    real = ipc.NativeDocument.call
    owned = []
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    def drop(self, operation, args, **kwargs):
        if operation == 'build':
            owned.append(self.process)
            # Represent already-confirmed work and an ambiguous posted attempt.
            first = self.ledger.reserve_attempt('confirmed', .02)
            self.ledger.reconcile_attempt(first, .01)
            self.ledger.reserve_attempt('unknown', .02)
            monkeypatch.setattr(resources, 'measure', lambda root: (0, 8 * 1024**3))
            self.resource_lease.next_check = 0
        return real(self, operation, args, **kwargs)
    monkeypatch.setattr(ipc.NativeDocument, 'call', drop)
    try:
        task = _run(rig, mode='full', replace_existing_epub=True)
        assert task.stat == STAT_ENDED and 'capacity' in task.message.lower()
        row = _ledger_rows(rig)[0]
        assert row['status'] == 'cancelled' and row['pending_usd'] == pytest.approx(.02)
        assert row['spend_usd'] == pytest.approx(.01)
        assert owned and owned[0].poll() is not None and unrelated.poll() is None
        assert prior.read_bytes() == b'prior' and rig.local_db.session.commits == 0
        assert not list(rig.folder.glob('.reflow-*-staging'))
    finally:
        unrelated.terminate(); unrelated.wait(timeout=5)


def test_cancelled_admission_and_latched_stop_release_safely(tmp_path, monkeypatch):
    with pytest.raises(resources.AttemptCancelled):
        resources.Lease(tmp_path, lambda: True)
    with resources.Lease(tmp_path) as lease:
        monkeypatch.setattr(resources, 'measure', lambda root: (0, 8 * 1024**3))
        with pytest.raises(resources.ResourceStopped):
            lease.check(force=True)
        monkeypatch.setattr(resources, 'measure', lambda root: (8 * 1024**3, 8 * 1024**3))
        with pytest.raises(resources.ResourceStopped):
            lease.check(force=True)
    with resources.Lease(tmp_path):
        pass


def test_inherited_descriptor_keeps_lease_until_last_owned_process_exits(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    lease = resources.Lease(tmp_path)
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                             stdin=subprocess.PIPE, pass_fds=(lease.fd,))
    try:
        lease.close()  # equivalent kernel descriptor closure on owner death
        with pytest.raises(resources.ResourceUnavailable, match='busy'):
            resources.Lease(tmp_path)
        child.stdin.close(); child.wait(timeout=5)
        with resources.Lease(tmp_path):
            pass
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)


def test_cgroup_ancestor_limit_and_host_headroom_both_apply(tmp_path):
    proc = tmp_path / 'proc'; (proc / 'self').mkdir(parents=True)
    group = tmp_path / 'cgroup'; leaf = group / 'service'; leaf.mkdir(parents=True)
    (proc / 'meminfo').write_text('MemAvailable: 8388608 kB\n')
    (proc / 'self/cgroup').write_text('0::/service\n')
    mount_name = str(group).replace(' ', r'\040')
    (proc / 'self/mountinfo').write_text(f'1 0 0:1 / {mount_name} rw - cgroup2 cgroup rw\n')
    (leaf / 'memory.max').write_text('max'); (leaf / 'memory.current').write_text('100')
    (group / 'memory.max').write_text(str(3 * 1024**3)); (group / 'memory.current').write_text(str(2 * 1024**3))
    assert resources.linux_headroom(proc) == 1024**3
    (proc / 'meminfo').write_text('MemAvailable: 100 kB\n')
    assert resources.linux_headroom(proc) == 102400
    (group / 'memory.current').unlink()
    with pytest.raises(OSError):
        resources.linux_headroom(proc)


def test_other_process_ownership_releases_on_abrupt_death(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    # Fresh exec, no forked native state; only a tiny stdlib lock holder.
    code = ('import fcntl,os,sys; '
            'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); '
            'fcntl.flock(fd,fcntl.LOCK_EX); print("owned",flush=True); sys.stdin.read()')
    owner = subprocess.Popen([sys.executable, '-c', code, str(tmp_path / 'native-resource.lock')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert owner.stdout.readline() == b'owned\n'
        with pytest.raises(resources.ResourceUnavailable, match='busy'):
            resources.Lease(tmp_path)
        owner.kill(); owner.wait(timeout=5)
        with resources.Lease(tmp_path):
            pass
    finally:
        if owner.poll() is None:
            owner.kill(); owner.wait(timeout=5)
        owner.stdin.close(); owner.stdout.close()


def test_quote_drop_reaps_owned_process_and_does_not_make_ready(tmp_path, monkeypatch):
    worker = tmp_path / 'inert_quote.py'
    worker.write_text('import sys,time; sys.stdin.read(); time.sleep(30)')
    monkeypatch.setattr(quote_preparation, 'WORKER', worker)
    real = quote_preparation.subprocess.Popen
    children = []
    def launch(*args, **kwargs):
        children.append(real(*args, **kwargs))
        monkeypatch.setattr(resources, 'measure', lambda root: (0, 8 * 1024**3))
        return children[-1]
    monkeypatch.setattr(quote_preparation.subprocess, 'Popen', launch)
    with pytest.raises(resources.ResourceStopped):
        quote_preparation.measure_isolated('unused', {}, None, lambda: False, tmp_path, grace=.05)
    assert len(children) == 1 and children[0].poll() is not None
    assert not list((tmp_path / 'quote-scratch').iterdir())
    monkeypatch.setattr(resources, 'measure', lambda root: (8 * 1024**3, 8 * 1024**3))
    with resources.Lease(tmp_path):
        pass


def test_parent_stop_checkpoint_prevents_provider_dispatch(rig, monkeypatch):
    from cps.services.reflow import model
    monkeypatch.setattr(rig.mod.config, 'resolved_openrouter_key', lambda: 'inert-not-a-key')
    sent = []
    monkeypatch.setattr(model.requests, 'post', lambda *a, **k: sent.append(k))
    real = rig.mod.TaskReflowPdf._convert
    def low(self, doc, client, ledger, cache):
        monkeypatch.setattr(resources, 'measure', lambda root: (0, 8 * 1024**3))
        doc.resource_lease.next_check = 0
        return real(self, doc, client, ledger, cache)
    monkeypatch.setattr(rig.mod.TaskReflowPdf, '_convert', low)
    task = _run(rig, mode='full', review_mode='source_verified', cost_cap_usd=1)
    assert task.stat == STAT_ENDED and not sent
    assert rig.local_db.session.commits == 0


def test_admission_requires_all_three_samples_and_checked_is_default(tmp_path, monkeypatch):
    monkeypatch.delenv('REFLOW_NATIVE_CAPACITY_MODE', raising=False)
    readings = iter([(8 * 1024**3, 8 * 1024**3), (0, 8 * 1024**3)])
    monkeypatch.setattr(resources, 'measure', lambda root: next(readings))
    with pytest.raises(resources.ResourceUnavailable, match='insufficient'):
        resources.Lease(tmp_path)
    monkeypatch.setattr(resources, 'measure', lambda root: (None, None))
    with pytest.raises(resources.ResourceUnavailable, match='unknown'):
        resources.Lease(tmp_path)
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    with resources.Lease(tmp_path):
        pass


def test_exclusion_probe_reports_bounded_child_phases_and_parent_reap(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    records = []
    monkeypatch.setattr(resources.log, 'info', lambda message, *args: records.append(message % args))
    with resources.Lease(tmp_path):
        pass
    assert len(records) == 1
    assert 'outcome=passed' in records[0]
    assert 'child_phases=' in records[0] and '1:' in records[0]
    assert 'pid=' in records[0] and 'reaped_ms=' in records[0]
    assert str(tmp_path) not in records[0]


@pytest.mark.parametrize('script,expected_phase', [
    ('import time; time.sleep(5)', 'none'),
    ('import os,time; os.write(2, f"1:{time.monotonic_ns()}\\n".encode()); time.sleep(5)', '1:'),
])
def test_exclusion_timeout_distinguishes_pre_phase_and_child_stage(tmp_path, monkeypatch, script, expected_phase):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    # Leave room for interpreter startup on a loaded test host; both fixture
    # delays are five seconds. Production's two-second deadline is untouched.
    monkeypatch.setattr(resources, 'PROBE_TIMEOUT', .8)
    probe = tmp_path / 'delayed_probe.py'; probe.write_text(script)
    monkeypatch.setattr(resources, 'LOCK_PROBE', probe)
    warnings, launched = [], []
    monkeypatch.setattr(resources.log, 'warning', lambda message, *args: warnings.append(message % args))
    original = resources.Popen
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        launched.append(child)
        return child
    monkeypatch.setattr(resources, 'Popen', launch)
    with pytest.raises(resources.ResourceUnavailable, match='timed out'):
        resources.Lease(tmp_path)
    assert launched and launched[0].poll() is not None
    assert len(warnings) == 1 and 'outcome=timeout' in warnings[0]
    assert f'child_phases={expected_phase}' in warnings[0]


def test_probe_logging_failure_cannot_mask_valid_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    def broken(*args):
        raise OSError('inert logging error')
    monkeypatch.setattr(resources.log, 'info', broken)
    with resources.Lease(tmp_path):
        pass


def test_probe_drains_oversized_child_stderr_without_logging_it(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    probe = tmp_path / 'noisy_probe.py'
    probe.write_text('import os; os.write(2, b"private" * 20000); raise SystemExit(3)')
    monkeypatch.setattr(resources, 'LOCK_PROBE', probe)
    warnings = []
    monkeypatch.setattr(resources.log, 'warning', lambda message, *args: warnings.append(message % args))
    with pytest.raises(resources.ResourceUnavailable, match='probe failed'):
        resources.Lease(tmp_path)
    assert len(warnings) == 1 and 'outcome=failed' in warnings[0]
    assert 'private' not in warnings[0] and 'child_phases=none' in warnings[0]


def test_probe_warning_sink_failure_preserves_timeout_and_reaps(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    monkeypatch.setattr(resources, 'PROBE_TIMEOUT', .12)
    probe = tmp_path / 'delayed_probe.py'; probe.write_text('import time; time.sleep(5)')
    monkeypatch.setattr(resources, 'LOCK_PROBE', probe)
    launched = []
    original = resources.Popen
    def launch(*args, **kwargs):
        child = original(*args, **kwargs); launched.append(child); return child
    monkeypatch.setattr(resources, 'Popen', launch)
    monkeypatch.setattr(resources.log, 'warning', lambda *args: (_ for _ in ()).throw(OSError('inert sink')))
    with pytest.raises(resources.ResourceUnavailable, match='timed out'):
        resources.Lease(tmp_path)
    assert launched and launched[0].poll() is not None


def test_probe_spawn_failure_keeps_original_refusal_and_scalar_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    warnings = []
    monkeypatch.setattr(resources.log, 'warning', lambda message, *args: warnings.append(message % args))
    def refused(*args, **kwargs):
        raise OSError('private path and detail')
    monkeypatch.setattr(resources, 'Popen', refused)
    with pytest.raises(resources.ResourceUnavailable, match='could not start'):
        resources.Lease(tmp_path)
    assert len(warnings) == 1 and 'outcome=spawn_failed' in warnings[0]
    assert 'private' not in warnings[0]


def test_probe_capture_setup_failure_keeps_valid_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    def unavailable(*args):
        raise OSError('inert telemetry failure')
    monkeypatch.setattr(resources.os, 'set_blocking', unavailable)
    with resources.Lease(tmp_path):
        pass


def test_estimate_resource_refusal_is_503_not_bad_pdf(rig, monkeypatch):
    from cps.api import reflow as api
    from tests.unit.test_reflow_api import _ctx, _status, _json
    monkeypatch.setattr(api, '_require_edit', lambda: None)
    monkeypatch.setattr(api, '_source_or_error', lambda bid: (rig.book, str(rig.folder / 'Book - Author.pdf'), None))
    monkeypatch.setattr(api, 'REFLOW_DIR', rig.root)
    monkeypatch.setattr(resources, 'measure', lambda root: (None, None))
    with _ctx('/api/v1/books/5/reflow/estimate'):
        response = api.reflow_estimate.__wrapped__(5)
        assert _status(response) == 503
        assert _json(response)['error']['code'] == 'native_capacity_unavailable'


def test_accepted_preparation_refusal_keeps_terminal_owner_visible(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'inert')
    store = quote_preparation.PreparationStore(tmp_path / 'quotes')
    def refused(*args):
        raise resources.ResourceUnavailable('busy')
    try:
        job = store.start(7, 5, source, {}, refused)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = store.get(7, 5, job['preparation_id'])
            if result['status'] == 'failed':
                break
            time.sleep(.01)
        assert result['status'] == 'failed'
        assert result['error'] == 'native_capacity_unavailable'
        with pytest.raises(KeyError):
            store.get(8, 5, job['preparation_id'])
    finally:
        store.close()
