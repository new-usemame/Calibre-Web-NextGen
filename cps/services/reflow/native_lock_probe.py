"""Inert fresh-exec flock contender. No app imports, inherited lock or secrets.

Exit 0 only for observed independent-open exclusion on the expected inode;
42 means exclusion failed; 3 means unknown/error. Never create a lock file.
Keep acquisition distinct from the interpreter's own startup/usage exit codes.
"""
import os
import time


def _mark(phase):
    # Fixed numeric stderr only. A closed diagnostic pipe cannot change the
    # lock test or its exit protocol.
    try:
        os.write(2, f'{phase}:{time.monotonic_ns()}\n'.encode('ascii'))
    except OSError:
        pass


_mark(1)  # Python reached this file; interpreter startup before this is unobserved.
import errno
import fcntl
import stat
import sys
_mark(2)  # Imports complete.


def main():
    fd = None
    try:
        path, device, inode = sys.argv[1:]
        _mark(3)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        _mark(4)
        info = os.fstat(fd)
        _mark(5)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (int(device), int(inode)):
            return 3
        try:
            _mark(6)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            _mark(7)
            return 0 if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else 3
        _mark(7)
        return 42
    except (OSError, ValueError):
        return 3
    finally:
        if fd is not None:
            os.close(fd)
        _mark(8)


if __name__ == '__main__':
    status = main()
    _mark(9)
    raise SystemExit(status)
