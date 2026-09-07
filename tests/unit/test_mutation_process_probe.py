# SPDX-License-Identifier: GPL-3.0-or-later
"""The diagnostic EIO counter must retain returned inconclusive observations."""
import ctypes
import errno
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_diagnostic_probe_counts_uninspectable_processes(monkeypatch, capsys):
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **k: SimpleNamespace(sysctl=lambda *a: -1))
    monkeypatch.setattr(ctypes, 'get_errno', lambda: errno.EIO)
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: '123\n')
    ticks = iter((0, 0))
    monkeypatch.setattr(time, 'monotonic', lambda: next(ticks, 61))
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    monkeypatch.setitem(sys.modules, 'eio_probe_harness', None)
    script = Path(__file__).resolve().parents[1] / 'mutation' / 'inspect_process_args.py'
    runpy.run_path(str(script))
    output = capsys.readouterr().out
    assert 'INCONCLUSIVE pid=123 errno=5' in output
    assert 'scans=1 probes=1 EIO=1' in output
    assert 'RAISE' not in output
    print(output, end='')
