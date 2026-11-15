# SPDX-License-Identifier: GPL-3.0-or-later
"""Process ownership for the optional Calibre server, without Flask imports.

The supervisor retains the owner lock until its child has exited. Its stdin
is a lifeline owned only by the app: EOF on app exit/exec kills and reaps the
child. A maintenance process owns a separate lock for its entire run; the
supervisor stops Calibre when that lock is busy, including after app restart.
"""
import importlib.util
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_services = Path(__file__).resolve().parent / "services"
_locks = _load("_cwng_server_file_lock", _services / "file_lock.py")
_writes = _load("_cwng_server_write_lock", _services / "calibre_db_lock.py")
EXIT_MAINTENANCE = 75


class LibraryBusyError(RuntimeError):
    """Another cooperating operation owns the library; retain input for retry."""


def operation(config_dir=None, timeout=120):
    """The existing cooperating-writer gate also protects server transitions."""
    return _writes.metadata_db_write_lock(
        lock_dir=os.environ.get("CWA_METADATA_LOCK_DIR") or config_dir, timeout=timeout)


def _open_lock(config_dir, kind):
    return _locks.open_lock(os.path.join(config_dir, ".cwa-content-server-{}.lock".format(kind)))


def busy(config_dir, kind):
    if not os.path.exists(os.path.join(config_dir, ".cwa-content-server-{}.lock".format(kind))):
        return False
    fd = _open_lock(config_dir, kind)
    try:
        if not _locks.acquire(fd, blocking=False):
            return True
        _locks.release(fd)
        return False
    finally:
        os.close(fd)


@contextmanager
def maintenance(config_dir, timeout=120, wait_timeout=1):
    """Own maintenance before accessing the library; do not steal another run."""
    fd = _open_lock(config_dir, "maintenance")
    acquired = False
    try:
        acquire_deadline = time.monotonic() + min(timeout, wait_timeout)
        while not _locks.acquire(fd, blocking=False):
            if time.monotonic() >= acquire_deadline:
                raise LibraryBusyError("Calibre library maintenance is already running")
            time.sleep(0.05)
        acquired = True
        deadline = time.monotonic() + timeout
        while busy(config_dir, "owner"):
            if time.monotonic() >= deadline:
                raise TimeoutError("Managed Calibre server did not stop for library maintenance")
            time.sleep(0.1)
        yield fd
    finally:
        if acquired:
            _locks.release(fd)
        os.close(fd)


def _reap(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=4)


def _linux_parent_death():
    # The supervisor has no threads when Popen calls this hook.
    import ctypes
    import signal
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set Calibre parent death signal")
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)


def supervise(config_dir, command):
    """Run one child while the parent lifeline and maintenance policy allow it."""
    fd = _open_lock(config_dir, "owner")
    child = None
    acquired = False
    try:
        owner_deadline = time.monotonic() + 0.2
        while not _locks.acquire(fd, blocking=False):
            if time.monotonic() >= owner_deadline:
                raise LibraryBusyError("A managed Calibre server already owns this configuration")
            time.sleep(0.02)
        acquired = True
        if busy(config_dir, "maintenance"):
            return EXIT_MAINTENANCE
        kwargs = {}
        if os.name != "nt":
            # The child retains ownership if this supervisor is killed. Never
            # explicitly unlock the shared open-file description before reap.
            kwargs["pass_fds"] = (fd,)
        if sys.platform.startswith("linux"):
            kwargs["preexec_fn"] = _linux_parent_death
        child = subprocess.Popen(command, **kwargs)
        parent_gone = threading.Event()

        def lifeline():
            # A daemon blocked on BufferedReader.read can abort Python during
            # interpreter shutdown. Raw fd I/O owns no buffered-reader lock.
            while os.read(sys.stdin.fileno(), 1):
                pass
            parent_gone.set()

        threading.Thread(target=lifeline, daemon=True).start()
        # SIGTERM must execute the reap path, not leave the child orphaned.
        import signal
        previous = signal.signal(signal.SIGTERM, lambda *_args: parent_gone.set())
        maintenance_drained = False
        try:
            while child.poll() is None:
                if parent_gone.wait(0.1):
                    _reap(child)
                    break
                if busy(config_dir, "maintenance"):
                    try:
                        # A parent stop owns the gate and then signals us.
                        # Poll rather than wait indefinitely, so its lifeline
                        # or SIGTERM can interrupt a maintenance drain.
                        with operation(config_dir, timeout=0.2):
                            _reap(child)
                        maintenance_drained = True
                        break
                    except TimeoutError:
                        continue
            if maintenance_drained:
                return EXIT_MAINTENANCE
            # The reserved guardian status must never turn a real child crash
            # into a maintenance deferral.
            if child.returncode == EXIT_MAINTENANCE:
                print("Calibre child exited with reserved status 75", flush=True)
                return 1
            return child.returncode
        finally:
            signal.signal(signal.SIGTERM, previous)
            _reap(child)
    finally:
        # Release ownership only after a confirmed reap. An exceptional reap
        # fails closed: the OS releases the lock when this supervisor exits.
        if child is None or child.poll() is not None:
            if acquired:
                _locks.release(fd)
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1], sys.argv[2:]))
