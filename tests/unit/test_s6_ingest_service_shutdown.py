# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioural SIGTERM coverage for the cwa-ingest s6 longrun.

The service installs a TERM trap because it owns several background helpers.
Bash defers a trapped signal while a foreground command is running, however,
so the watcher itself must run asynchronously and be waited for explicitly.
These tests execute the real run script with a blocking watcher stub, rather
than pinning a particular shell spelling, because the regression was precisely
that the trap looked correct while never being dispatched.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = (
    REPO_ROOT
    / "root"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "cwa-ingest-service"
    / "run"
)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _session_processes(session_id: int) -> list[str]:
    """Return every process still belonging to the service's OS session.

    Unlike the watcher PID file, this does not require a child to cooperate.
    An orphan keeps its session ID even after it is reparented, and closing the
    service's stdout/stderr cannot hide it from this check.
    """
    result = subprocess.run(
        ["ps", "-axo", "pid=,sess=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    survivors = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) >= 2 and int(fields[1]) == session_id:
            survivors.append(line.strip())
    return survivors


def _kill_service_session(session_id: int) -> None:
    for _ in range(10):
        survivors = _session_processes(session_id)
        if not survivors:
            return
        for process_line in survivors:
            pid = int(process_line.split(maxsplit=1)[0])
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.02)


def _assert_service_session_drained(session_id: int) -> None:
    deadline = time.monotonic() + 1
    survivors = _session_processes(session_id)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.02)
        survivors = _session_processes(session_id)
    assert not survivors, f"processes survived in service session {session_id}: {survivors}"


@pytest.mark.parametrize(
    ("network_share_mode", "expected_watcher", "ignore_term"),
    [
        ("true", "watch_fallback.py", False),
        ("false", "inotifywait", False),
        ("true", "watch_fallback.py", True),
    ],
    ids=["polling-fallback", "inotify", "unresponsive-watcher"],
)
def test_sigterm_stops_ingest_service_and_its_watcher_tree(
    tmp_path: Path,
    network_share_mode: str,
    expected_watcher: str,
    ignore_term: bool,
):
    """TERM must stop both watcher paths without waiting for s6's SIGKILL.

    ``cwa-as-abc`` is the only external boundary stubbed here. It records the
    command selected by the real service, then creates a child and blocks just
    like the production Python/inotify watchers. The service must exit within
    two seconds and leave neither level behind.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    watcher_ready = tmp_path / "watcher.ready"
    watcher_pids = tmp_path / "watcher.pids"
    watcher_invocations = tmp_path / "watcher.invocations"
    cwa_as_abc = bin_dir / "cwa-as-abc"
    cwa_as_abc.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$WATCHER_READY_FILE\"\n"
        "printf '%s\\n' \"$*\" >> \"$WATCHER_INVOCATIONS_FILE\"\n"
        "printf '%s\\n' \"$BASHPID\" >> \"$WATCHER_PID_FILE\"\n"
        "if [ \"${WATCHER_IGNORE_TERM:-0}\" = 1 ]; then trap '' TERM; fi\n"
        "sleep 30 &\n"
        "printf '%s\\n' \"$!\" >> \"$WATCHER_PID_FILE\"\n"
        "wait \"$!\"\n"
    )
    cwa_as_abc.chmod(0o755)

    watch_folder = tmp_path / "ingest"
    watch_folder.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WATCH_FOLDER": str(watch_folder),
            "NETWORK_SHARE_MODE": network_share_mode,
            "CWA_WATCH_MODE": "inotify",
            "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry.queue"),
            "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
            "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
            "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
            "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch.dirty"),
            "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch.success"),
            "CWA_INGEST_BATCH_QUIET_SECONDS": "60",
            "WATCHER_READY_FILE": str(watcher_ready),
            "WATCHER_PID_FILE": str(watcher_pids),
            "WATCHER_INVOCATIONS_FILE": str(watcher_invocations),
            "WATCHER_IGNORE_TERM": "1" if ignore_term else "0",
        }
    )

    process = subprocess.Popen(
        ["bash", str(RUN_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    timed_out = False
    try:
        deadline = time.monotonic() + 3
        while not watcher_ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("ingest watcher did not start within three seconds")
            time.sleep(0.02)

        assert process.poll() is None, "ingest service exited before SIGTERM"
        assert expected_watcher in watcher_ready.read_text()

        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        if process.poll() is None:
            _kill_service_session(process.pid)
        output = process.communicate(timeout=2)[0]

    assert not timed_out, (
        "cwa-ingest-service was still alive two seconds after SIGTERM; "
        "s6 would eventually SIGKILL the container. Output:\n" + output
    )
    assert process.returncode == 0, output

    recorded_pids = [int(line) for line in watcher_pids.read_text().splitlines()]
    deadline = time.monotonic() + 1
    while any(_pid_exists(pid) for pid in recorded_pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    survivors = [pid for pid in recorded_pids if _pid_exists(pid)]
    assert not survivors, f"watcher descendants survived service shutdown: {survivors}"
    _assert_service_session_drained(process.pid)

    if expected_watcher == "inotifywait":
        selected = watcher_invocations.read_text().splitlines()
        assert len(selected) == 1, (
            "TERM must kill the inotify watcher group, not turn its failure into "
            f"a polling failover during shutdown: {selected}"
        )
        assert "inotifywait" in selected[0]


@pytest.mark.parametrize(
    ("network_share_mode", "publication_stage"),
    [
        ("true", "post_batch"),
        ("false", "stale_cleanup"),
        ("true", "fallback"),
        ("false", "inotify"),
    ],
    ids=["post-batch-helper", "stale-cleanup-helper", "polling-fallback", "inotify"],
)
def test_sigterm_during_pid_publication_does_not_orphan_group(
    tmp_path: Path,
    network_share_mode: str,
    publication_stage: str,
):
    """TERM in the exact ``&`` -> ``$!`` window must be replayed after publish."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cwa_as_abc = bin_dir / "cwa-as-abc"
    cwa_as_abc.write_text(
        "#!/usr/bin/env bash\n"
        "trap '' TERM\n"
        "bash -c 'trap \"\" TERM; exec sleep 30' &\n"
        "wait \"$!\"\n"
    )
    cwa_as_abc.chmod(0o755)

    hook_ready = tmp_path / "publication.ready"
    hook_release = tmp_path / "publication.release"
    watch_folder = tmp_path / "ingest"
    watch_folder.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WATCH_FOLDER": str(watch_folder),
            "NETWORK_SHARE_MODE": network_share_mode,
            "CWA_WATCH_MODE": "inotify",
            "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry.queue"),
            "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
            "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
            "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
            "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch.dirty"),
            "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch.success"),
            "CWA_INGEST_BATCH_QUIET_SECONDS": "60",
            "CWA_INGEST_PID_PUBLICATION_TEST_STAGE": publication_stage,
            "CWA_INGEST_PID_PUBLICATION_TEST_READY": str(hook_ready),
            "CWA_INGEST_PID_PUBLICATION_TEST_RELEASE": str(hook_release),
        }
    )
    process = subprocess.Popen(
        ["bash", str(RUN_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    try:
        deadline = time.monotonic() + 3
        while not hook_ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("service never entered the watcher PID-publication window")
            time.sleep(0.01)
        assert process.poll() is None
        process.send_signal(signal.SIGTERM)
        hook_release.touch()
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            _kill_service_session(process.pid)
        output = process.communicate(timeout=2)[0]

    assert process.returncode == 0, output
    _assert_service_session_drained(process.pid)


@pytest.mark.parametrize(
    ("survival_probes", "expected_probes", "expect_warning"),
    [(3, 4, False), (99, 20, True)],
    ids=["eventually-drains", "remains-stuck"],
)
def test_stop_process_group_polls_for_group_drain_after_sigkill(
    tmp_path: Path,
    survival_probes: int,
    expected_probes: int,
    expect_warning: bool,
):
    """A reaped leader must not substitute for checking the whole killed group.

    The fake process API models both an eventually drained group and one that
    remains observable throughout the bound, as can happen while a descendant
    is stuck in kernel I/O. Real D-state is neither safe nor deterministic to
    manufacture in a unit test; overriding Bash's process builtins makes the
    drain contract deterministic while executing the real function body.
    """
    harness = f"""
set -e
export CWA_INGEST_SERVICE_TEST_MODE=1
export WATCH_FOLDER={str(tmp_path)!r}
export CWA_INGEST_RETRY_QUEUE={str(tmp_path / 'retry.queue')!r}
export CWA_INGEST_STATUS_FILE={str(tmp_path / 'status')!r}
export CWA_INGEST_PROCESSING_DIR={str(tmp_path / 'processing')!r}
export CWA_INGEST_RECENT_DIR={str(tmp_path / 'recent')!r}
export CWA_INGEST_BATCH_DIRTY_FILE={str(tmp_path / 'batch.dirty')!r}
export CWA_INGEST_BATCH_LAST_SUCCESS_FILE={str(tmp_path / 'batch.success')!r}
source {str(RUN_SCRIPT)!r}
killed=0
post_kill_probes=0
kill() {{
    if [ "$1" = "-KILL" ]; then killed=1; return 0; fi
    if [ "$1" = "-0" ]; then
        if [ "$killed" = 1 ]; then
            post_kill_probes=$((post_kill_probes + 1))
            if [ "$post_kill_probes" -le {survival_probes} ]; then return 0; fi
            return 1
        fi
        return 0
    fi
    return 0
}}
sleep() {{ :; }}
wait() {{ :; }}
stop_process_group 4242
printf 'post_kill_probes=%s\\n' "$post_kill_probes"
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"post_kill_probes={expected_probes}" in result.stdout
    assert ("remained after SIGKILL" in result.stderr) is expect_warning


def test_inotify_failure_still_falls_back_to_polling(tmp_path: Path):
    """Backgrounding the terminal watcher must not change ``||`` failover."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocations = tmp_path / "watcher.invocations"
    watcher_pids = tmp_path / "watcher.pids"
    cwa_as_abc = bin_dir / "cwa-as-abc"
    cwa_as_abc.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$WATCHER_INVOCATIONS_FILE\"\n"
        "if [[ \" $* \" == *' inotifywait '* ]]; then exit 23; fi\n"
        "printf '%s\\n' \"$BASHPID\" >> \"$WATCHER_PID_FILE\"\n"
        "sleep 30 &\n"
        "printf '%s\\n' \"$!\" >> \"$WATCHER_PID_FILE\"\n"
        "wait \"$!\"\n"
    )
    cwa_as_abc.chmod(0o755)

    watch_folder = tmp_path / "ingest"
    watch_folder.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WATCH_FOLDER": str(watch_folder),
            "NETWORK_SHARE_MODE": "false",
            "CWA_WATCH_MODE": "inotify",
            "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry.queue"),
            "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
            "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
            "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
            "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch.dirty"),
            "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch.success"),
            "CWA_INGEST_BATCH_QUIET_SECONDS": "60",
            "WATCHER_INVOCATIONS_FILE": str(invocations),
            "WATCHER_PID_FILE": str(watcher_pids),
        }
    )
    process = subprocess.Popen(
        ["bash", str(RUN_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    try:
        deadline = time.monotonic() + 3
        selected = []
        while time.monotonic() < deadline and process.poll() is None:
            if invocations.exists():
                selected = invocations.read_text().splitlines()
                if len(selected) >= 2:
                    break
            time.sleep(0.02)

        assert process.poll() is None, "service exited instead of failing over"
        assert len(selected) >= 2, f"polling fallback never started: {selected}"
        assert "inotifywait" in selected[0]
        assert "watch_fallback.py" in selected[1]
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            _kill_service_session(process.pid)
        output = process.communicate(timeout=2)[0]

    assert process.returncode == 0, output
    recorded_pids = [int(line) for line in watcher_pids.read_text().splitlines()]
    deadline = time.monotonic() + 1
    while any(_pid_exists(pid) for pid in recorded_pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not [pid for pid in recorded_pids if _pid_exists(pid)]
