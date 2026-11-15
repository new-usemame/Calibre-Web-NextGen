# SPDX-License-Identifier: GPL-3.0-or-later
"""Root maintenance helpers must leave locks usable by the app account."""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile


GUARD = "/app/calibre-web-automated/cps/calibre_server_guard.py"


def main(root_directory=False):
    spec = importlib.util.spec_from_file_location("guard", GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    account = pwd.getpwnam("abc")
    with tempfile.TemporaryDirectory(prefix="cwng-root-lock-") as directory:
        if root_directory:
            os.chmod(directory, 0o777)
        else:
            os.chown(directory, account.pw_uid, account.pw_gid)
        with guard.operation(directory):
            pass
        for kind in ("maintenance", "owner"):
            os.close(guard._open_lock(directory, kind))
        owners = [path.stat().st_uid for path in Path(directory).iterdir()]
        if len(owners) != 3 or any(uid != account.pw_uid for uid in owners):
            raise RuntimeError("root helper stranded a lock outside the app account")
        child = f"""import importlib.util, os
spec = importlib.util.spec_from_file_location('guard', {GUARD!r})
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
with guard.operation({directory!r}, timeout=.2):
    pass
for kind in ('maintenance', 'owner'):
    fd = guard._open_lock({directory!r}, kind)
    if not guard._locks.acquire(fd, blocking=False):
        raise RuntimeError('root helper left a busy lock')
    guard._locks.release(fd)
    os.close(fd)
print('APP_LOCK_ACCESS_PASS')
"""
        result = subprocess.run(
            ["cwa-as-abc", "python3", "-c", child],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or result.stdout.strip() != "APP_LOCK_ACCESS_PASS":
            raise RuntimeError(f"app lock access failed: {result.stdout}\n{result.stderr}")
        print("CWNG_ROOT_LOCK_RUNTIME=" + json.dumps({
            "root_created_locks": 3,
            "service_owned_locks": len(owners),
            "app_acquired_all_locks": True,
            "root_owned_directory": root_directory,
        }))


if __name__ == "__main__":
    import sys
    main("--root-directory" in sys.argv)
