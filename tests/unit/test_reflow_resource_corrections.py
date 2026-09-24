"""Independent-review regressions: actual OCR launcher and visible cgroup views."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from cps.services.reflow import native_ipc as ipc, native_resources as resources

pytestmark = pytest.mark.unit


def _stopped(pid):
    # A killed orphan may briefly remain a zombie; it owns no memory or FDs.
    deadline = time.monotonic() + 5
    while True:
        status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                                capture_output=True, text=True).stdout.strip()
        if not status or status.startswith('Z'):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.02)


def _emergency_cleanup(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_all_visible_cgroup_ancestors_and_true_root(tmp_path):
    proc = tmp_path / 'proc'
    (proc / 'self').mkdir(parents=True)
    full, view = tmp_path / 'full', tmp_path / 'view'
    leaf = full / 'service'
    leaf.mkdir(parents=True)
    view.mkdir()
    (proc / 'meminfo').write_text('MemAvailable: 8388608 kB\n')
    (proc / 'self/cgroup').write_text('0::/service\n')
    def row(root, mount):
        return f'1 0 0:1 {root} {str(mount).replace(" ", r"\040")} rw - cgroup2 cgroup rw\n'
    fullrow, subrow = row('/', full), row('/service', view)
    (full / 'memory.max').write_text(str(3 * 1024**3))
    (full / 'memory.current').write_text(str(2 * 1024**3))
    for directory in (leaf, view):
        (directory / 'memory.max').write_text('max')
        (directory / 'memory.current').write_text('100')
    for mounts in (fullrow, fullrow + subrow, subrow + fullrow):
        (proc / 'self/mountinfo').write_text(mounts)
        assert resources.linux_headroom(proc) == 1024**3


@pytest.mark.parametrize('root, valid', [('/service', False), ('/', True)])
def test_missing_controller_files_require_hierarchy_root(tmp_path, root, valid):
    proc, mount = tmp_path / 'proc', tmp_path / 'mount'
    (proc / 'self').mkdir(parents=True)
    mount.mkdir()
    (proc / 'meminfo').write_text('MemAvailable: 8388608 kB\n')
    (proc / 'self/cgroup').write_text(f'0::{root}\n')
    escaped = str(mount).replace(' ', r'\040')
    (proc / 'self/mountinfo').write_text(f'1 0 0:1 {root} {escaped} rw - cgroup2 cgroup rw\n')
    (mount / 'cgroup.type').write_text('domain')
    if valid:
        assert resources.linux_headroom(proc) == 8 * 1024**3
    else:
        with pytest.raises((ValueError, OSError)):
            resources.linux_headroom(proc)


@pytest.mark.parametrize('stop', ['reserve', 'worker-death'])
def test_actual_ocr_launcher_contained_and_holds_lease(tmp_path, monkeypatch, stop):
    # Fresh worker imports the real launcher, but invokes only an inert sleeper.
    package = Path(ipc.__file__).parent
    marker = tmp_path / 'ocr.json'
    worker = tmp_path / 'worker.py'
    worker.write_text(f'''
import os, sys, json, importlib.util, importlib
from pathlib import Path
root = Path({str(package)!r})
spec = importlib.util.spec_from_file_location('_owned', root/'__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
codec = importlib.import_module('_owned.native_codec')
ocr = importlib.import_module('_owned.ocr')
control = os.fdopen(int(sys.argv[3]), 'w', buffering=1)
sys.stdin.readline()
Path('response.json').write_bytes(codec.dumps({{'seq':1, 'ok':True, 'value':1}}))
control.write(json.dumps({{'seq':1, 'ready':True}})+'\\n')
sys.stdin.readline()
code = "import os,time,json;from pathlib import Path;Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp()}}));time.sleep(30)"
parent = int(sys.argv[1])
ocr._invoke([sys.executable, '-c', code], Path.cwd(), timeout=60, should_stop=lambda: os.getppid()!=parent)
''')
    monkeypatch.setattr(ipc, 'WORKER', worker)
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'inert input; no PDF parser used')
    doc = ipc.NativeDocument(source, scratch_root=tmp_path/'scratch', cache_root=tmp_path)
    real_cancel = doc._cancel
    witnessed = []
    def stop_on_marker():
        if marker.exists() and not witnessed:
            info = json.loads(marker.read_text())
            witnessed.append(info)
            if stop == 'reserve':
                monkeypatch.setattr(resources, 'measure', lambda root: (0, 8*1024**3))
            else:
                doc.process.kill()
                doc.process.wait(timeout=5)
                # Remove parent's copy too: OCR alone must keep ownership alive.
                doc.resource_lease.close()
                with pytest.raises(resources.ResourceBusy):
                    with resources.Lease(tmp_path):
                        pass
        return real_cancel()
    monkeypatch.setattr(doc, '_cancel', stop_on_marker)
    try:
        with pytest.raises((resources.ResourceStopped, ipc.ChildExited)):
            doc.call('prepare', {})
    finally:
        doc.close()
        # Safe cleanup even when run against the broken baseline. Only PID/group
        # freshly emitted by our owned sleeper; never a stale campaign PID.
        if marker.exists():
            info = json.loads(marker.read_text())
            stopped_by_product = _stopped(info['pid'])
            _emergency_cleanup(info['pid'])
    assert witnessed and witnessed[0]['group'] == doc.process.pid
    assert stopped_by_product, 'product cleanup left OCR alive (test emergency cleanup followed)'
    assert doc.process.returncode is not None
    monkeypatch.setattr(resources, 'measure', lambda root: (8*1024**3, 8*1024**3))
    # Kernel descriptor close can follow kill delivery; bounded polling is an
    # assertion deadline, not a product grace-period increase.
    deadline = time.monotonic() + 5
    while True:
        try:
            with resources.Lease(tmp_path):
                break
        except resources.ResourceBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def test_quote_launcher_stops_real_ocr_group(tmp_path, monkeypatch):
    from cps.services.reflow import quote_preparation as quote
    marker, worker = tmp_path/'ocr.json', tmp_path/'quote.py'
    package = Path(ipc.__file__).parent
    worker.write_text(f'''
import os, sys, json, importlib.util, importlib
from pathlib import Path
root = Path({str(package)!r})
spec = importlib.util.spec_from_file_location('_owned',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
ocr=importlib.import_module('_owned.ocr')
request=json.load(sys.stdin)
code="import os,time,json;from pathlib import Path;Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp(),'worker':os.getppid()}}));time.sleep(30)"
ocr._invoke([sys.executable,'-c',code],Path.cwd(),timeout=60,should_stop=lambda:Path(request['control']).exists() or os.getppid()!=request['parent'])
''')
    monkeypatch.setattr(quote, 'WORKER', worker)
    monkeypatch.setattr(resources, 'measure', lambda root: (0 if marker.exists() else 8*1024**3, 8*1024**3))
    try:
        with pytest.raises(resources.ResourceStopped):
            quote.measure_isolated(tmp_path/'unused.pdf', {}, None, lambda:False, tmp_path, grace=.2)
    finally:
        if marker.exists():
            info = json.loads(marker.read_text())
            stopped_by_product = _stopped(info['pid'])
            _emergency_cleanup(info['pid'])
    assert info['group'] == info['worker']
    assert stopped_by_product
    monkeypatch.setattr(resources, 'measure', lambda root:(8*1024**3,8*1024**3))
    with resources.Lease(tmp_path):
        pass


def test_standalone_ocr_session_and_invalid_supervision(tmp_path, monkeypatch):
    from cps.services.reflow import ocr
    monkeypatch.delenv('REFLOW_NATIVE_LEASE_FD', raising=False)
    status, output, _ = ocr._invoke([sys.executable, '-c',
        'import os,json;print(json.dumps([os.getpid(),os.getpgrp()]))'],
        tmp_path, timeout=5, should_stop=None)
    pid, group = json.loads(output)
    assert status == 0 and pid == group and group != os.getpgrp()
    monkeypatch.setenv('REFLOW_NATIVE_LEASE_FD', 'not-a-descriptor')
    launched = []
    monkeypatch.setattr(ocr.subprocess, 'Popen', lambda *a, **k: launched.append(a))
    with pytest.raises(ocr.OCRUnavailable, match='ownership'):
        ocr._invoke(['never'], tmp_path, timeout=5, should_stop=None)
    assert not launched
