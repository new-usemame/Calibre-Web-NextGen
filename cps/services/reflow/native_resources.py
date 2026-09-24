"""Deployment-scoped, cross-process admission for all Reflow native launchers.

No daemon or PID stale-file guessing. The lock inode is permanent; its open file
description is inherited by the owned child until it exits. Measurements are
admission/stop signals, never a promise that the kernel cannot OOM-kill the parent.
"""
import fcntl
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time

from .model import AttemptCancelled

MIB = 1024 * 1024


class ResourceUnavailable(RuntimeError):
    pass


class ResourceBusy(ResourceUnavailable):
    pass


class ResourceStopped(AttemptCancelled):
    pass


def _read(path):
    with open(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError('capacity metadata too large')
    return data.strip()


def _unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def linux_headroom(proc=Path('/proc')):
    """Minimum host available RAM and visible cgroup-v2 ancestor headroom.

    v1/hybrid memory controllers and unreadable metadata are explicitly unknown.
    A hidden ancestor outside the namespace cannot be measured here; deployment
    must expose its effective limit or reserve that capacity externally.
    """
    values = dict(line.split(':', 1) for line in _read(proc / 'meminfo').splitlines())
    host = values['MemAvailable'].split()
    if len(host) != 2 or host[1] != 'kB':
        raise ValueError('invalid MemAvailable')
    available = int(host[0]) * 1024
    memberships = [line.split(':', 2) for line in _read(proc / 'self/cgroup').splitlines()]
    if any('memory' in row[1].split(',') for row in memberships):
        raise ValueError('cgroup-v1 memory measurement unsupported')
    unified = [row[2] for row in memberships if row[:2] == ['0', '']]
    if len(unified) != 1 or not unified[0].startswith('/') or '..' in PurePosixPath(unified[0]).parts:
        raise ValueError('unknown cgroup membership')
    mounts = []
    for line in _read(proc / 'self/mountinfo').splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        if right.split()[0] == 'cgroup2':
            root, mount = map(_unescape, fields[3:5])
            try:
                relative = PurePosixPath(unified[0]).relative_to(root)
            except ValueError:
                continue
            mounts.append((PurePosixPath(root), Path(mount), relative))
    if not mounts:
        raise ValueError('cgroup mount unavailable')
    # A second subtree mount must never hide an ancestor visible through another
    # mount. Inspect every applicable view; missing data in any view is unknown.
    for hierarchy_root, mount, relative in mounts:
        current = mount / relative
        while True:
            maximum, usage = current / 'memory.max', current / 'memory.current'
            # A mountpoint is not necessarily the hierarchy root. Namespace-
            # hidden ancestors remain a deployment limitation, not free memory.
            if (hierarchy_root == PurePosixPath('/') and current == mount
                    and not maximum.exists() and not usage.exists()):
                # cgroup.type, like memory.max/current, is a non-root ABI.
                # Require readable controller availability, not a domain marker
                # (which also exists on non-root subtree mountpoints).
                if 'memory' not in _read(current / 'cgroup.controllers').split():
                    raise ValueError('unknown root controller')
            else:
                limit, used = _read(maximum), int(_read(usage))
                if used < 0:
                    raise ValueError('invalid cgroup usage')
                if limit != 'max':
                    cap = int(limit)
                    if cap < 0:
                        raise ValueError('invalid cgroup limit')
                    available = min(available, max(0, cap - used))
            if current == mount:
                break
            current = current.parent
    if available < 0:
        raise ValueError('invalid host capacity')
    return available


def measure(root):
    try:
        memory = linux_headroom() if sys.platform.startswith('linux') else None
        return memory, shutil.disk_usage(root).free
    except (OSError, ValueError, KeyError, IndexError):
        return None, None


def _mib(name, default):
    try:
        value = int(os.environ.get('REFLOW_NATIVE_' + name + '_MIB', default))
        if value <= 0:
            raise ValueError()
        return value * MIB
    except ValueError:
        raise ResourceUnavailable('Invalid native resource capacity configuration') from None


class Lease:
    """One native job across conversion, preparation and survey, fail-fast busy.

    Keep this object alive through parent provider/audit/publication checkpoints.
    Close only after owned children have been reaped. Never unlink its lock file.
    """
    def __init__(self, root, should_stop=None):
        self.root = Path(root)
        self.should_stop = should_stop
        self.fd = None
        self.reason = None
        self.next_check = 0
        self.mode = os.environ.get('REFLOW_NATIVE_CAPACITY_MODE', 'checked')
        if self.mode not in ('checked', 'serialize-only'):
            raise ResourceUnavailable('Unknown native resource capacity mode')
        self.admit = _mib('ADMIT', 2048)
        self.reserve = _mib('RESERVE', 512)
        self.disk_admit = _mib('SCRATCH_ADMIT', 2048)
        self.disk_reserve = _mib('SCRATCH_RESERVE', 256)
        if self.reserve >= self.admit or self.disk_reserve >= self.disk_admit:
            raise ResourceUnavailable('Native stop reserve must be below admission capacity')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.root / 'native-resource.lock',
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                raise ResourceUnavailable('Native resource lock must be a regular file')
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ResourceBusy('Native resource busy; retry after the active work completes') from None
            for sample in range(3):
                self._cancel()
                self._capacity(self.admit, self.disk_admit, ResourceUnavailable)
                if sample < 2 and self.mode == 'checked':
                    time.sleep(.1)
        except BaseException:
            self.close()
            raise

    def _cancel(self):
        if self.should_stop and self.should_stop():
            raise AttemptCancelled('Native resource admission cancelled')

    def _capacity(self, memory_floor, disk_floor, error):
        if self.mode == 'serialize-only':
            return
        memory, disk = measure(self.root)
        if memory is None or disk is None:
            raise error('Native capacity unknown; checked deployment requires readable RAM/cgroup and scratch measurements')
        if memory < memory_floor or disk < disk_floor:
            raise error('Native capacity insufficient; preserving parent reserve and original artifact')

    def check(self, force=False):
        self._cancel()
        if self.reason:
            raise ResourceStopped(self.reason)
        if force or time.monotonic() >= self.next_check:
            try:
                self._capacity(self.reserve, self.disk_reserve, ResourceStopped)
            except ResourceStopped as exc:
                self.reason = str(exc)
                raise
            self.next_check = time.monotonic() + .5
        return False

    def before_launch(self):
        # Source snapshot copying may take time after the initial samples.
        self._cancel()
        self._capacity(self.admit, self.disk_admit, ResourceUnavailable)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)  # child inheritance retains ownership after parent death
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
