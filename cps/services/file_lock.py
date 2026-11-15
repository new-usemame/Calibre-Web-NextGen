# SPDX-License-Identifier: GPL-3.0-or-later
"""Exclusive locks for persistent, non-truncating lock files.

Prefer POSIX flock; otherwise lock byte zero with Windows msvcrt (including
on empty files). Callers must use separate descriptors for independent holders
and must not share a descriptor while acquiring/releasing. The file position is
preserved. Blocking acquisition waits until acquired; non-blocking acquisition
returns False only on contention. Other errors propagate.

When neither backend exists, acquisition explicitly degrades to a logged no-op
and returns True. There is then NO cross-process exclusion: run only one app
process and avoid concurrent restore/service writers. Release is a no-op too.
The bespoke cooperative calibre_db_lock protocol is intentionally separate.
"""
import errno
import importlib.util
import logging
import os
from pathlib import Path
import stat
import time

try:
    import fcntl
except ImportError:
    fcntl = None

msvcrt = None
if fcntl is None:
    try:
        import msvcrt
    except ImportError:
        pass

log = logging.getLogger(__name__)
_BUSY = (errno.EACCES, errno.EAGAIN)


def open_lock(path, mode=0o600):
    """Open a dedicated lock inode shared by root helpers and its directory owner.

    Root-run container helpers must not strand a lock owned by root in the
    service user's configuration directory. Never follow a symlink or transfer
    a hard-linked inode while repairing that ownership.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        info = os.fstat(fd)
        linked = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_ISLNK(linked.st_mode)
                or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)):
            raise OSError(errno.EINVAL, "Lock must be a dedicated regular file", path)
        if getattr(os, "geteuid", lambda: -1)() == 0 and info.st_uid == 0:
            directory = os.stat(os.path.dirname(os.path.abspath(path)))
            if directory.st_uid != 0:
                os.fchown(fd, directory.st_uid, directory.st_gid)
            else:
                # Network-share mode can leave /config owned by root. Reuse
                # the standalone helpers' configured account, changing only
                # this dedicated lock rather than the mounted directory.
                helper = Path(__file__).resolve().parents[2] / "scripts" / "service_user.py"
                if helper.is_file():
                    spec = importlib.util.spec_from_file_location("_cwng_lock_service_user", helper)
                    service_user = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(service_user)
                    ids = service_user.service_ids()
                    if ids is not None:
                        os.fchown(fd, *ids)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _windows_lock(fd, mode):
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def acquire(fd, *, blocking=True):
    """Return True when acquired (or degraded), False when non-blocking/busy."""
    if fcntl is not None:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except OSError as error:
            if not blocking and error.errno in _BUSY:
                return False
            raise
        return True
    if msvcrt is not None:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        while True:
            try:
                _windows_lock(fd, mode)
                return True
            except OSError as error:
                if error.errno not in _BUSY and not (
                    blocking and error.errno == errno.EDEADLK
                ):
                    raise
                if not blocking:
                    return False
                # LK_LOCK gives up after ten retries. Preserve flock's
                # indefinite blocking contract, without spinning on failure.
                time.sleep(0.1)
    log.warning(
        "File locking is a no-op: neither fcntl nor msvcrt is available; "
        "cross-process exclusion is disabled. Use a single app process and "
        "avoid concurrent restore/service writers."
    )
    return True


def release(fd):
    """Release a successfully acquired lock; backend errors propagate."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:
        _windows_lock(fd, msvcrt.LK_UNLCK)
