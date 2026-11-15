# SPDX-License-Identifier: GPL-3.0-or-later
"""Check built-image Calibre authentication, ownership and Linux parent death.

All test libraries, accounts, subprocesses and ports are private and temporary.
Product modules are loaded from the image, never copied from the host.
Explicit checks also work under optimized Python.
"""

import ctypes
import importlib.util
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time

APP_ROOT = pathlib.Path("/app/calibre-web-automated")
if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:
    raise RuntimeError("subreaper setup failed")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


guard = load("guard", str(APP_ROOT / "cps/calibre_server_guard.py"))
policy = load("policy", str(APP_ROOT / "cps/calibre_library_target.py"))
probe_children = load(
    "probe_children", str(pathlib.Path(__file__).with_name("calibre_runtime_probe_children.py"))
)


def wait(check, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise RuntimeError("bounded condition was not reached")


def live(pid):
    try:
        return pathlib.Path("/proc/%s/stat" % pid).read_text().split()[2] not in (
            "Z",
            "X",
        )
    except FileNotFoundError:
        return False


results = []
with tempfile.TemporaryDirectory(prefix="cwng-calibre-life-") as base:
    runtime_config = pathlib.Path(base) / "calibre-config"
    runtime_config.mkdir()
    runtime_env = dict(os.environ, CALIBRE_CONFIG_DIRECTORY=str(runtime_config))
    library = pathlib.Path(base) / "library"
    library.mkdir()
    initialize = subprocess.run(
        [
            "/usr/bin/calibre-debug",
            "-c",
            "from calibre.db.legacy import LibraryDatabase; d=LibraryDatabase(%r); d.close()"
            % str(library),
        ],
        capture_output=True,
        text=True,
        env=runtime_env,
    )
    if initialize.returncode:
        raise RuntimeError(initialize.stderr)
    for event in ("lifeline-eof", "supervisor-kill"):
        config = pathlib.Path(base) / event
        config.mkdir()
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            port = port_probe.getsockname()[1]
        command = [
            "/usr/bin/calibre-server",
            str(library),
            "--port",
            str(port),
            "--listen-on",
            "127.0.0.1",
            "--disable-auth",
            "--enable-local-write",
            "--max-jobs",
            "1",
        ]
        authenticated = event == "lifeline-eof"
        if authenticated:
            userdb = config / "users.sqlite"
            created = subprocess.run(
                [
                    "/usr/bin/calibre-debug",
                    "-e",
                    str(APP_ROOT / "scripts/calibre_server_user.py"),
                    "--",
                    str(userdb),
                    "fixture-user",
                ],
                input="Fixture-2026  \n",
                capture_output=True,
                text=True,
                env=runtime_env,
            )
            if created.returncode:
                raise RuntimeError("user database creation failed: " + created.stderr)
            command = [
                value
                for value in command
                if value not in ("--disable-auth", "--enable-local-write")
            ]
            command += ["--enable-auth", "--userdb", str(userdb)]
        supervisor = subprocess.Popen(
            [
                sys.executable,
                str(APP_ROOT / "cps/calibre_server_guard.py"),
                str(config),
                *command,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(runtime_env, XDG_CACHE_HOME=str(config / "cache")),
        )
        child = None
        try:
            wait(lambda: policy.server_ready("127.0.0.1", port, str(library)))
            if authenticated:
                address = "http://127.0.0.1:{}/#library".format(port)
                client = [
                    "/usr/bin/calibre-debug",
                    "-e",
                    str(APP_ROOT / "cps/calibredb_pipe.py"),
                    "--",
                    "list",
                    "--for-machine",
                    "--with-library",
                    address,
                    "--username",
                    "fixture-user",
                    "--password",
                    "<stdin>",
                ]
                accepted = subprocess.run(
                    client,
                    input="Fixture-2026  \n",
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=runtime_env,
                )
                if accepted.returncode or json.loads(accepted.stdout) != []:
                    raise RuntimeError(
                        "authenticated pipe client did not list the empty library"
                    )
                rejected = subprocess.run(
                    client,
                    input="Wrong-fixture-value\n",
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=runtime_env,
                )
                if rejected.returncode == 0:
                    raise RuntimeError("wrong password was accepted")
            children = (
                pathlib.Path(
                    "/proc/%s/task/%s/children" % (supervisor.pid, supervisor.pid)
                )
                .read_text()
                .split()
            )
            if len(children) != 1:
                raise RuntimeError("unexpected managed child count")
            child = int(children[0])
            inherited = [
                str(p.resolve()) for p in pathlib.Path("/proc/%s/fd" % child).iterdir()
            ]
            if str(config / ".cwa-content-server-owner.lock") not in inherited:
                raise RuntimeError("actual Calibre launcher discarded owner descriptor")
            if not guard.busy(str(config), "owner"):
                raise RuntimeError("actual server lacks owner lock")
            if event == "lifeline-eof":
                supervisor.stdin.close()
            else:
                supervisor.kill()
            supervisor.wait(timeout=10)
            wait(lambda: not live(child))
            if event == "supervisor-kill":
                os.waitpid(child, 0)
            wait(lambda: not guard.busy(str(config), "owner"))
            results.append(
                {
                    "event": event,
                    "authenticated_client": authenticated,
                    "wrong_password_rejected": authenticated,
                    "ready": True,
                    "actual_launcher_owner_fd": True,
                    "child_reaped": True,
                    "owner_released": True,
                }
            )
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait(timeout=5)
            if child and live(child):
                os.kill(child, signal.SIGKILL)
                wait(lambda: not live(child))
            if child:
                try:
                    os.waitpid(child, 0)
                except ChildProcessError:
                    pass
            if not supervisor.stdin.closed:
                supervisor.stdin.close()
            # SIGKILL can leave Calibre's safe_atexit pipe worker finishing
            # against our private config/cache paths. As the subreaper, this
            # probe owns its adopted helpers; drain them before directory removal.
            adopted = pathlib.Path("/proc/%s/task/%s/children" % (os.getpid(), os.getpid()))
            helper_statuses = probe_children.drain_owned_children(
                lambda: [int(pid) for pid in adopted.read_text().split()]
            )
            if results and results[-1]["event"] == event:
                results[-1]["adopted_helpers_reaped"] = len(helper_statuses)
print(
    "CWNG_SERVER_RUNTIME="
    + json.dumps(
        {
            "calibre": "9.11",
            "results": results,
            "cleanup": "all exact owned child PIDs reaped; temporary library removed",
        }
    )
)
