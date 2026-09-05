# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded EPERM reproduction; log group identity/state, never argv or environments."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
source = (HERE / 'mutate.py').read_text()
needle = '''        os.killpg(pgid, sig)
    except ProcessLookupError:'''
replacement = '''        os.killpg(pgid, sig)
    except PermissionError:
        import inspect
        frame = inspect.currentframe().f_back
        proc = frame.f_locals.get('proc')
        table = subprocess.run(['ps', '-axo', 'pid=,pgid=,uid=,state='],
            capture_output=True, text=True, timeout=5)
        members = [line.split() for line in table.stdout.splitlines()
                   if len(line.split()) == 4 and line.split()[1] == str(pgid)]
        try:
            os.killpg(pgid, 0)
            exists = 'permission-probe-ok'
        except OSError as probe_error:
            exists = 'errno-' + str(probe_error.errno)
        print('EPERM_AT_RAISE ' + json.dumps(dict(pgid=pgid, signal=int(sig),
            group_probe=exists, members_pid_pgid_uid_state=members,
            direct_child_returncode=proc.returncode if proc else 'unknown',
            direct_child_reaped=(proc.returncode is not None) if proc else 'unknown',
            table_returncode=table.returncode)), flush=True)
        raise
    except ProcessLookupError:'''
assert source.count(needle) == 1
with tempfile.TemporaryDirectory(prefix='mutation-eperm-probe-') as temp:
    directory = Path(temp)
    for name in ('provenance_probe.py', 'pytest_evidence.py'):
        shutil.copyfile(HERE / name, directory / name)
    harness = directory / 'mutate.py'
    harness.write_text(source.replace(needle, replacement))
    result = subprocess.run([sys.executable, '-m', 'pytest',
        'tests/unit/test_mutation_harness.py::test_checked_real_outcomes_are_durable_and_unverified[timeout]',
        '-q', '-s', '-o', 'addopts=', '-p', 'no:rerunfailures', '-p', 'no:flaky', '--tb=no'],
        cwd=HERE.parents[1], env={**os.environ, 'CWNG_CHECK_MUTANT': str(harness)},
        text=True, capture_output=True, timeout=120)
    print(result.stdout, end='')
    print(f'PROBE_EXIT={result.returncode} (Mac/APFS only)')

# A control owns exactly one child in a fresh group. Keep its zombie unreaped
# until both group and individual permission probes have been observed.
import signal
import time
child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                         stdin=subprocess.PIPE, start_new_session=True)
try:
    os.killpg(child.pid, 0)
    print('CONTROL live own group: killpg(signal=0) OK')
    child.stdin.write(b'x')
    child.stdin.close()
    deadline = time.monotonic() + 10
    while True:
        state = subprocess.check_output(['ps', '-p', str(child.pid), '-o', 'state='], text=True).strip()
        if state.startswith('Z'):
            break
        if time.monotonic() > deadline:
            raise RuntimeError('control did not become a zombie')
        time.sleep(.01)
    os.kill(child.pid, 0)
    print('CONTROL own unreaped zombie: kill(pid, 0) OK')
    for sig in (0, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
            print(f'CONTROL zombie-only group: killpg(signal={int(sig)}) OK')
        except OSError as exc:
            print(f'CONTROL zombie-only group: killpg(signal={int(sig)}) errno={exc.errno}')
finally:
    child.wait(timeout=10)
print('CONTROL direct child reaped: returncode=' + str(child.returncode))
