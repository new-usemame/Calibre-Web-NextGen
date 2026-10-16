"""Cross-process storage capability, not a marker or successful flock call."""
import subprocess
import sys
import errno
import json
import time

import pytest
from cps.services.reflow import native_resources as r, native_ipc as ipc
from cps.services.reflow.model import AttemptCancelled

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('mode', ['checked', 'serialize-only'])
def test_lying_parent_flock_refuses_before_heavy_launch(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', mode)
    # Parent reports success but takes no actual lock. The real independent
    # process can acquire this inode, exactly the unsafe property being tested.
    monkeypatch.setattr(r.fcntl, 'flock', lambda *args: None)
    launched = []
    def heavy(*a, **k):
        launched.append(a)
        raise AssertionError('Heavy worker reached on non-exclusive storage')
    monkeypatch.setattr(ipc.subprocess, 'Popen', heavy)
    source = tmp_path/'inert.pdf'
    source.write_bytes(b'not opened: refused before PDF worker launch')
    with pytest.raises(r.ResourceUnavailable, match='exclusion'):
        ipc.NativeDocument(source, scratch_root=tmp_path/'scratch', cache_root=tmp_path)
    assert not launched


def test_actual_probe_repeats_and_busy_stays_busy(tmp_path, monkeypatch, record_property):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    durations = []
    for _ in range(2):
        start = time.perf_counter()
        with r.Lease(tmp_path):
            durations.append(time.perf_counter() - start)
            with pytest.raises(r.ResourceBusy):
                r.Lease(tmp_path)
    record_property('serialize_only_admission_seconds', json.dumps(durations))
    # A prior successful check must not authorize a later broken substrate.
    monkeypatch.setattr(r.fcntl, 'flock', lambda *args: None)
    with pytest.raises(r.ResourceUnavailable, match='exclusion'):
        with r.Lease(tmp_path):
            pass


@pytest.mark.parametrize('script', ['raise SystemExit(3)', 'raise SystemExit(42)', 'import time; time.sleep(30)'])
def test_probe_error_acquisition_timeout_reaped(tmp_path, monkeypatch, script):
    probe = tmp_path/'probe.py'
    probe.write_text(script)
    monkeypatch.setattr(r, 'LOCK_PROBE', probe)
    monkeypatch.setattr(r, 'PROBE_TIMEOUT', .15)
    owned = []
    real = r.Popen
    def launch(*a, **kw):
        process = real(*a, **kw)
        owned.append(process)
        return process
    monkeypatch.setattr(r, 'Popen', launch)
    with pytest.raises(r.ResourceUnavailable, match='exclusion'):
        r.Lease(tmp_path)
    assert len(owned) == 1 and owned[0].poll() is not None
    # Failure releases only the parent's owned lock; no stale state blocks retry.
    monkeypatch.undo()
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    with r.Lease(tmp_path):
        pass


def test_probe_cancel_reaps_and_releases(tmp_path, monkeypatch):
    probe = tmp_path/'probe.py'
    probe.write_text('import time; time.sleep(30)')
    monkeypatch.setattr(r, 'LOCK_PROBE', probe)
    owned = []
    real = r.Popen
    def launch(*a, **kw):
        process = real(*a, **kw)
        owned.append(process)
        return process
    monkeypatch.setattr(r, 'Popen', launch)
    with pytest.raises(AttemptCancelled):
        r.Lease(tmp_path, lambda: bool(owned))
    assert len(owned) == 1 and owned[0].poll() is not None


def test_missing_probe_is_unknown_not_claimed_acquisition(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'LOCK_PROBE', tmp_path/'missing.py')
    with pytest.raises(r.ResourceUnavailable, match='unverified'):
        r.Lease(tmp_path)


def test_real_probe_rejects_wrong_inode_and_open_error(tmp_path):
    path = tmp_path/'lock'
    path.touch()
    info = path.stat()
    for target, inode in ((path, info.st_ino + 1), (tmp_path/'missing', info.st_ino)):
        result = subprocess.run([sys.executable, '-I', str(r.LOCK_PROBE), str(target),
                                 str(info.st_dev), str(inode)], timeout=5)
        assert result.returncode == 3


@pytest.mark.parametrize('error', [errno.EACCES, errno.EIO])
def test_probe_lock_errors_are_not_credited_as_exclusion(tmp_path, monkeypatch, error):
    from cps.services.reflow import native_lock_probe as probe
    path = tmp_path/'lock'
    path.touch()
    info = path.stat()
    monkeypatch.setattr(sys, 'argv', ['probe', str(path), str(info.st_dev), str(info.st_ino)])
    def broken(*args):
        raise OSError(error, 'unverified substrate')
    monkeypatch.setattr(probe.fcntl, 'flock', broken)
    assert probe.main() == 3


def test_replaced_lock_after_admission_refuses_launch(tmp_path, monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')
    with r.Lease(tmp_path) as lease:
        path = tmp_path/'native-resource.lock'
        path.rename(tmp_path/'old-owned-lock')
        path.touch()
        with pytest.raises(r.ResourceUnavailable, match='identity'):
            lease.before_launch()
