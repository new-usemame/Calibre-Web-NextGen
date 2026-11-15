# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Windows' missing APIs in isolated interpreters (PR #2614)."""
import os
from pathlib import Path
import queue
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _child(code):
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
        text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_services_import_without_posix_accounts_and_ingest_keeps_exclusion():
    """Missing POSIX modules must neither boot Flask nor disable ingest locking."""
    _child('''
import subprocess, sys, os, tempfile, types
try:
    import fcntl as host_lock
except ImportError:
    host_lock = None
sys.path.insert(0, "scripts")
sys.modules.update({"pwd": None, "grp": None, "fcntl": None})
for name in ("geteuid", "fchmod"):
    if hasattr(os, name):
        delattr(os, name)
# Use real host locks behind the Windows interface to check backend selection
# and descriptor contention, rather than an always-successful lock stub.
windows = types.ModuleType("msvcrt")
windows.LK_LOCK, windows.LK_NBLCK, windows.LK_UNLCK = 1, 2, 0
calls = []
def locking(fd, mode, size):
    calls.append(mode)
    assert os.lseek(fd, 0, os.SEEK_CUR) == 0 and size == 1
    flags = {0: host_lock.LOCK_UN, 1: host_lock.LOCK_EX,
             2: host_lock.LOCK_EX | host_lock.LOCK_NB}[mode]
    host_lock.flock(fd, flags)
windows.locking = locking
if host_lock is not None:
    sys.modules["msvcrt"] = windows
import service_user
assert service_user.service_ids() is None
assert service_user.chown_to_service_user("unused", "[test]") is False
import ingest_processor
assert "cps" not in sys.modules, "standalone import initialized Flask"
with tempfile.TemporaryDirectory() as root:
    first = ingest_processor.ProcessLock()
    second = ingest_processor.ProcessLock()
    first.lock_path = second.lock_path = os.path.join(root, "ingest.lock")
    try:
        assert first.acquire(timeout=0.2)
        assert second.acquire(timeout=0.1) is False
        assert second.lock_file is None
    finally:
        first.release()
    assert second.acquire(timeout=0.2)
    second.release()
    if host_lock is not None:
        assert windows.LK_NBLCK in calls and windows.LK_UNLCK in calls
''')


def test_kobo_import_without_fork_registration():
    """Enabling Kobo must not crash startup on a platform without fork."""
    _child('''
import importlib, os
from cps.services import kobo_patch_spool
if hasattr(os, "register_at_fork"):
    del os.register_at_fork
importlib.reload(kobo_patch_spool)
assert kobo_patch_spool._REQUEST_IO_SLOTS.acquire(blocking=False)
kobo_patch_spool._REQUEST_IO_SLOTS.release()
''')


def test_enforcement_without_unix_uid_never_attempts_privilege_changes(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import cover_enforcer
    import metadata_change_dispatch as dispatch

    monkeypatch.delattr(os, "geteuid", raising=False)
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "cwa-as-abc")
    assert dispatch.enforcer_command("enforcer.py", "change.json") == [
        sys.executable, "enforcer.py", "--log", "change.json",
    ]
    monkeypatch.setattr(os, "chown", lambda *a: pytest.fail("chown without UID API"), raising=False)
    cover_enforcer.Enforcer._reset_book_dir_ownership(str(tmp_path))


@pytest.mark.parametrize("service,filename,arguments", [
    ("convert_library_start", "convert_library.py", []),
    ("epub_fixer_start", "kindle_epub_fixer.py", ["--all"]),
    ("epub_fixer_start", "kindle_epub_fixer.py", ["--input_file", "book with spaces.epub"]),
    ("cover_enforcer_start", "cover_enforcer.py", ["-all"]),
])
def test_admin_services_launch_with_the_running_interpreter(
    monkeypatch, tmp_path, service, filename, arguments,
):
    """All task launch paths retain interpreter and argv boundaries."""
    from cps import cwa_functions as cwa

    calls = []
    process = object()
    monkeypatch.setattr(cwa.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd) or process)
    monkeypatch.setattr(sys, "executable", "/venv with spaces/python.exe")
    monkeypatch.setattr(cwa, "_service_log_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(cwa.content_server, "hold_library", lambda: object())
    monkeypatch.setattr(cwa, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    result = queue.Queue()
    extra = [arguments[-1]] if "--input_file" in arguments else []
    getattr(cwa, service)(result, *extra)
    assert calls == [[sys.executable, os.path.join(cwa.constants.SCRIPTS_DIR, filename), *arguments]]
    assert result.get_nowait() is process


def test_refresh_launches_ingest_with_the_running_interpreter(monkeypatch):
    from cps import cwa_functions as cwa, app

    calls = []
    monkeypatch.setattr(cwa.subprocess, "run", lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(sys, "executable", "/venv with spaces/python.exe")
    monkeypatch.setattr(cwa, "get_ingest_dir", lambda: "ingest with spaces")
    monkeypatch.setattr(cwa, "_l", lambda text: text)
    monkeypatch.setitem(app.config, "library_refresh_messages", [])
    cwa.refresh_library(app)
    assert calls == [[sys.executable, os.path.join(cwa.constants.SCRIPTS_DIR, "ingest_processor.py"), "ingest with spaces"]]
    assert len(app.config["library_refresh_messages"]) == 1
