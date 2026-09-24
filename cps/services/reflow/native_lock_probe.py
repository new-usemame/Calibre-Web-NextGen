"""Inert fresh-exec flock contender. No app imports, inherited lock or secrets.

Exit 0 only for observed independent-open exclusion on the expected inode;
42 means exclusion failed; 3 means unknown/error. Never create a lock file.
Keep acquisition distinct from the interpreter's own startup/usage exit codes.
"""
import errno
import fcntl
import os
import stat
import sys


def main():
    fd = None
    try:
        path, device, inode = sys.argv[1:]
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (int(device), int(inode)):
            return 3
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            return 0 if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else 3
        return 42
    except (OSError, ValueError):
        return 3
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
