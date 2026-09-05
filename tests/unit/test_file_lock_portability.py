# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #2168: exercise missing platform modules in fresh interpreters."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_child(code, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        env={**os.environ, "CALIBRE_DBPATH": str(tmp_path)},
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("module", [
    "cps.admin", "cps.services.kobo_exchange_capture",
    "cps.services.kobo_patch_spool",
])
def test_startup_import_without_fcntl(module, tmp_path):
    run_child(f'''
import sys
# None blocks even cached/extension-module imports, solely in this child.
sys.modules["fcntl"] = None
import importlib
importlib.import_module({module!r})
print("import succeeded: {module}")
''', tmp_path)


def test_windows_backend_contention_release_and_errors(tmp_path):
    output = run_child('''
import errno
import importlib.util
import os
import sys
import types

sys.modules["fcntl"] = None
import cps.admin as admin
from cps.services import kobo_exchange_capture as capture, kobo_patch_spool as spool
held = {}
calls = []
failures = []
fake = types.ModuleType("msvcrt")
fake.LK_LOCK, fake.LK_NBLCK, fake.LK_UNLCK = 1, 2, 0

def locking(fd, mode, length):
    offset = os.lseek(fd, 0, os.SEEK_CUR)
    assert (offset, length) == (0, 1)
    calls.append(mode)
    if failures:
        raise OSError(failures.pop(0), "injected failure")
    info = os.fstat(fd)
    key = (info.st_dev, info.st_ino, offset, length)
    if mode == fake.LK_UNLCK:
        assert held.pop(key) == fd
    elif key in held:
        raise OSError(errno.EACCES, "held by another descriptor")
    else:
        held[key] = fd

fake.locking = locking
sys.modules["msvcrt"] = fake
# Load the real helper without making unrelated stdlib imports select Windows.
spec = importlib.util.spec_from_file_location("portable_lock", "cps/services/file_lock.py")
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
# Separate opens of the same inode, including different starting offsets.
path = os.path.join(os.environ["CALIBRE_DBPATH"], "lock")
with open(path, "a+b") as first, open(path, "a+b") as second:
    first.seek(7)
    second.seek(19)
    assert lock.acquire(first.fileno()) is True
    assert lock.acquire(second.fileno(), blocking=False) is False
    assert first.tell() == 7 and second.tell() == 19
    lock.release(first.fileno())
    assert lock.acquire(second.fileno(), blocking=False) is True
    lock.release(second.fileno())
    assert calls == [1, 2, 0, 2, 0]
    assert not held
    # A timed-out LK_LOCK must continue waiting, not become a no-op.
    failures.append(errno.EDEADLK)
    assert lock.acquire(first.fileno()) is True
    lock.release(first.fileno())
    for blocking in (True, False):
        failures.append(errno.EBADF)
        try:
            lock.acquire(first.fileno(), blocking=blocking)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            raise AssertionError("unexpected error was swallowed")
    assert first.tell() == 7
# Drive all three real callers through the selected backend. Stop capture at
# the first protected storage operation: payload durability is a separate seam.
admin.file_lock = capture.file_lock = spool.file_lock = lock
if hasattr(os, "fchmod"):
    del os.fchmod
restore_path = os.path.join(os.environ["CALIBRE_DBPATH"], "restore.lock")
first = admin._acquire_restore_file_lock(restore_path)
assert first is not None
assert admin._acquire_restore_file_lock(restore_path) is None
admin._release_restore_locks([first])
root = __import__("pathlib").Path(os.environ["CALIBRE_DBPATH"]) / "spool"
root.mkdir()
calls.clear()
with spool._locked_root(root):
    assert held
assert calls == [1, 0] and not held
capture._capture_root = lambda: root
class ProtectedOperationReached(Exception):
    pass
def stop_inside_lock(*args, **kwargs):
    assert held
    raise ProtectedOperationReached()
capture._prune_locked = stop_inside_lock
session = capture.CaptureSession(
    exchange="test", method="POST", path="/test", query_string=b"",
    headers=[], body=b"[]", authentication="authenticated", user_id=1,
)
calls.clear()
try:
    session._persist()
except ProtectedOperationReached:
    pass
else:
    raise AssertionError("capture did not reach protected operation")
assert calls == [1, 0] and not held
print("msvcrt: blocking/nonblocking attempted; busy=False; release/reacquire; retry and errors passed")
''', tmp_path)
    assert "busy=False" in output


def test_no_backend_logs_explicit_degradation(tmp_path):
    run_child('''
import importlib.util
import io
import logging
import sys
sys.modules["fcntl"] = None
sys.modules["msvcrt"] = None
spec = importlib.util.spec_from_file_location("portable_lock", "cps/services/file_lock.py")
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
stream = io.StringIO()
handler = logging.StreamHandler(stream)
lock.log.addHandler(handler)
lock.log.setLevel(logging.WARNING)
assert lock.acquire(-1) is True
assert lock.acquire(-1, blocking=False) is True
lock.release(-1)
assert "neither fcntl nor msvcrt" in stream.getvalue()
assert "no-op" in stream.getvalue()
assert "cross-process exclusion is disabled" in stream.getvalue()
''', tmp_path)


def test_restore_lock_two_process_contention(tmp_path):
    import cps.admin as admin

    path = str(tmp_path / "restore.lock")
    holder = admin._acquire_restore_file_lock(path)
    assert holder is not None
    try:
        output = run_child(f'''
from cps.admin import _acquire_restore_file_lock
assert _acquire_restore_file_lock({path!r}) is None
print("second process: None while held")
''', tmp_path)
        assert "second process: None while held" in output
    finally:
        admin._release_restore_locks([holder])
    run_child(f'''
from cps.admin import _acquire_restore_file_lock, _release_restore_locks
handle = _acquire_restore_file_lock({path!r})
assert handle is not None
_release_restore_locks([handle])
''', tmp_path)
