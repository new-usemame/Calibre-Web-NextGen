"""Synthetic native snapshots and response sentinels, with real worker exits."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pymupdf
import pytest

from cps.services.reflow import native_ipc as ipc, native_resources as resources
from cps.services.reflow import retention

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def serialized_capacity(monkeypatch):
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'serialize-only')


def _pdf(path):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), 'Synthetic sample text for this test.')
        doc.save(path)
    return path


def _stopped_root(root):
    path = root / 'native-scratch' / 'native-abcd1234'
    path.mkdir(parents=True)
    (path / 'source.pdf').write_bytes(b'Synthetic source snapshot sentinel')
    (path / 'reader-response.json').write_text('{"content":"Synthetic response sentinel"}')
    return path


def test_retention_removes_response_and_source_after_real_owner_is_killed(tmp_path):
    """No age inference: the inherited resource lock survives the owner."""
    source = _pdf(tmp_path / 'input.pdf')
    root = tmp_path / 'state'
    ready = tmp_path / 'ready.json'
    script = '''
import json, sys, time
from pathlib import Path
from cps.services.reflow.native_ipc import NativeDocument
root, source, ready = map(Path, sys.argv[1:])
doc = NativeDocument(source, scratch_root=root/'native-scratch', cache_root=root)
(Path(doc.root)/'reader-response.json').write_text('{"content":"Synthetic response sentinel"}')
ready.write_text(json.dumps({'root': doc.root, 'worker_pid': doc.process.pid}))
while True:
    time.sleep(1)
'''
    owner = subprocess.Popen([sys.executable, '-P', '-c', script,
                              str(root), str(source), str(ready)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    scratch = None
    stopped_worker = None
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), owner.stderr.read() if owner.poll() is not None else 'owner did not start'
        evidence = json.loads(ready.read_text())
        assert evidence['worker_pid'] != owner.pid
        scratch = Path(evidence['root'])
        assert (scratch / 'source.pdf').read_bytes() == source.read_bytes()
        sentinel = scratch / 'reader-response.json'
        assert 'Synthetic response sentinel' in sentinel.read_text()
        # An ancient mtime cannot make a live worker's source removable.
        past = time.time() - 10 * retention.DAY
        os.utime(scratch, (past, past))
        assert retention.sweep(root).get('native-scratch', 0) == 0
        assert sentinel.exists()
        stopped_worker = evidence['worker_pid']
        os.kill(stopped_worker, signal.SIGSTOP)
        owner.kill()
        owner.wait(timeout=10)
        # The real worker is deliberately still alive after its owner exits.
        # The inherited descriptor must keep its source and response protected.
        assert retention.sweep(root).get('native-scratch', 0) == 0
        assert sentinel.exists()
        os.kill(stopped_worker, signal.SIGCONT)
        stopped_worker = None
        # EOF/parent detection terminates the real worker. Retention may acquire
        # the inherited lock only after its final native owner has exited.
        deadline = time.monotonic() + 30
        removed = 0
        while time.monotonic() < deadline:
            removed += retention.sweep(root).get('native-scratch', 0)
            if removed:
                break
            time.sleep(.02)
        assert removed == 1
        assert not scratch.exists()
        assert retention.sweep(root).get('native-scratch', 0) == 0
    finally:
        if stopped_worker is not None:
            try:
                os.kill(stopped_worker, signal.SIGCONT)
            except ProcessLookupError:
                pass
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        owner.stderr.close()


def test_next_native_admission_removes_stale_roots_before_creating_its_own(tmp_path):
    root = tmp_path / 'state'
    stale = _stopped_root(root)
    source = _pdf(tmp_path / 'input.pdf')
    with ipc.NativeDocument(source, scratch_root=root / 'native-scratch', cache_root=root) as doc:
        assert not stale.exists()
        assert (Path(doc.root) / 'source.pdf').read_bytes() == source.read_bytes()
        assert len(doc) == 1
        assert retention.sweep(root).get('native-scratch', 0) == 0
        assert Path(doc.root).exists()
    assert list((root / 'native-scratch').iterdir()) == []


def test_exclusive_recovery_keeps_its_lock_through_callback_and_removal(tmp_path):
    stale = _stopped_root(tmp_path)
    called = []
    def reconcile():
        called.append(True)
        assert not stale.exists()
        with pytest.raises(resources.ResourceBusy):
            resources.Lease(tmp_path)
    assert resources.recover_stale_scratch(tmp_path, on_exclusive=reconcile) == 1
    assert called == [True]
    with resources.Lease(tmp_path):
        pass


def test_inherited_live_lease_blocks_cleanup_even_when_parent_handle_is_closed(tmp_path):
    stale = _stopped_root(tmp_path)
    lease = resources.Lease(tmp_path)
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                             stdin=subprocess.PIPE, pass_fds=(lease.fd,))
    try:
        lease.close()
        called = []
        with pytest.raises(resources.ResourceBusy):
            resources.recover_stale_scratch(tmp_path, on_exclusive=lambda: called.append(True))
        assert called == [] and stale.exists()
        child.stdin.close()
        child.wait(timeout=10)
        assert resources.recover_stale_scratch(tmp_path) == 1
    finally:
        lease.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_full_or_unknown_capacity_does_not_prevent_safe_cleanup(tmp_path, monkeypatch):
    stale = _stopped_root(tmp_path)
    monkeypatch.setenv('REFLOW_NATIVE_CAPACITY_MODE', 'checked')
    monkeypatch.setattr(resources, 'measure', lambda root: (None, None))
    assert resources.recover_stale_scratch(tmp_path) == 1
    assert not stale.exists()
    with pytest.raises(resources.ResourceUnavailable, match='unknown'):
        resources.Lease(tmp_path)
    with resources.Lease(tmp_path, _cleanup_only=True) as lease:
        with pytest.raises(resources.ResourceUnavailable, match='cannot launch'):
            lease.before_launch()


def test_cleanup_never_follows_links_or_removes_unrecognised_names(tmp_path):
    root = tmp_path / 'state'
    stale = _stopped_root(root)
    outside = tmp_path / 'external'
    outside.mkdir()
    marker = outside / 'marker.txt'
    marker.write_text('Synthetic unrelated data.')
    scratch = stale.parent
    (scratch / 'native-link1234').symlink_to(outside, target_is_directory=True)
    (stale / 'nested-link').symlink_to(outside, target_is_directory=True)
    unowned = scratch / 'native-not-a-worker'
    unowned.mkdir()
    (unowned / 'keep').write_text('Synthetic unrelated data.')
    (scratch / 'native-file1234').write_text('Synthetic unrelated data.')
    assert resources.recover_stale_scratch(root) == 1
    assert marker.read_text() == 'Synthetic unrelated data.'
    assert (scratch / 'native-link1234').is_symlink()
    assert (unowned / 'keep').exists()
    assert (scratch / 'native-file1234').is_file()


def test_symlinked_scratch_root_is_refused_without_external_removal(tmp_path):
    root = tmp_path / 'state'
    root.mkdir()
    outside = tmp_path / 'external'
    stale = _stopped_root(outside)
    (root / 'native-scratch').symlink_to(stale.parent, target_is_directory=True)
    with pytest.raises(resources.ResourceUnavailable, match='owned directory'):
        resources.recover_stale_scratch(root)
    assert (stale / 'reader-response.json').exists()


def test_symlinked_deployment_root_is_refused_without_external_removal(tmp_path):
    outside = tmp_path / 'external'
    stale = _stopped_root(outside)
    root = tmp_path / 'state'
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(resources.ResourceUnavailable, match='owned directory'):
        resources.recover_stale_scratch(root)
    assert (stale / 'source.pdf').exists()


def test_cleanup_uses_lock_identity_instead_of_reusable_pid_checks(tmp_path, monkeypatch):
    stale = _stopped_root(tmp_path)
    # A stale PID may now identify another process, or its lookup may fail.
    # Neither affects proof supplied by the worker-inherited flock.
    def forbidden_lookup(*args):
        raise AssertionError('PID liveness is not a native ownership proof')
    monkeypatch.setattr(resources.os, 'kill', forbidden_lookup)
    assert resources.recover_stale_scratch(tmp_path) == 1
    assert not stale.exists()


def test_recovery_callback_failure_releases_lease_without_launching(tmp_path):
    stale = _stopped_root(tmp_path)
    def fail():
        raise ValueError('Synthetic recovery callback failure')
    with pytest.raises(ValueError, match='Synthetic recovery callback failure'):
        resources.recover_stale_scratch(tmp_path, on_exclusive=fail)
    assert not stale.exists()
    with resources.Lease(tmp_path):
        pass


def test_scratch_creation_occurs_only_under_admission_lock(tmp_path, monkeypatch):
    source = _pdf(tmp_path / 'input.pdf')
    root = tmp_path / 'state'
    real = ipc.tempfile.mkdtemp
    observed = []
    def create(*args, **kwargs):
        if kwargs.get('prefix') == 'native-':
            with pytest.raises(resources.ResourceBusy):
                resources.recover_stale_scratch(root)
            observed.append(True)
        return real(*args, **kwargs)
    monkeypatch.setattr(ipc.tempfile, 'mkdtemp', create)
    with ipc.NativeDocument(source, scratch_root=root / 'native-scratch', cache_root=root):
        pass
    assert observed == [True]
