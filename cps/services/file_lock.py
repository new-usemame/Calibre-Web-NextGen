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
import logging
import os
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
