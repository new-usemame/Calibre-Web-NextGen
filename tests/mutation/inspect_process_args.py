# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only bounded EIO probe; no process arguments or environments are logged."""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import types

source = Path(__file__).with_name('mutate.py').read_text()
needle = 'raise IsolationError(f"cannot inspect process environment: errno {error}")'
assert source.count(needle) == 1
source = source.replace(needle, '_raise_probe_error(pid, error, allocate, size.value)')
module = types.ModuleType('eio_probe_harness')
module.__file__ = str(Path(__file__).with_name('mutate.py'))
sys.modules[module.__name__] = module
exec(compile(source, '<instrumented-harness>', 'exec'), module.__dict__)
observations = []


def at_raise(pid, error, allocated, size):
    info = module._BsdInfo()
    library = ctypes.CDLL(None, use_errno=True)
    count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    comm = bytes(info.comm).split(b'\0')[0].decode(errors='replace') if count == ctypes.sizeof(info) else 'gone'
    # Only public executable names may enter a persistent transcript.
    allowed = {'Python', 'python', 'python3', 'node', 'git', 'ps', 'zsh', 'bash',
               'sh', 'rg', 'codex', 'claude', 'curl', 'gone'}
    label = comm if comm in allowed else 'redacted-' + hashlib.sha256(comm.encode()).hexdigest()[:10]
    observations.append((pid, error, allocated, size, label, info.start_sec, info.start_usec))
    print(f'RAISE pid={pid} comm={label} errno={error} buffer_allocated={allocated} size={size} identity_available={count == ctypes.sizeof(info)}', flush=True)
    raise module.IsolationError('recorded inspection error')


module._raise_probe_error = at_raise
deadline = time.monotonic() + 60
probes = 0
scans = 0
while time.monotonic() < deadline and not any(row[1] == errno.EIO for row in observations):
    table = subprocess.check_output(['ps', '-U', str(os.getuid()), '-o', 'pid='], text=True)
    scans += 1
    for value in table.split():
        try:
            module._has_phase_token(int(value), 'probe-only-no-signalling')
        except module.IsolationError:
            pass
        probes += 1
    time.sleep(.25)
print(f'OBSERVED scans={scans} probes={probes} EIO={sum(row[1] == errno.EIO for row in observations)} (Mac/APFS only)', flush=True)
