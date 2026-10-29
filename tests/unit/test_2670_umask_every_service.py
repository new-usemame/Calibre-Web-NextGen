# SPDX-License-Identifier: GPL-3.0-or-later
"""UMASK must reach every service that writes the library, not only svc-* (#2670).

@JohanSF and @sgreadly set ``UMASK=000`` / ``UMASK=027`` and still got 755
folders from ingest. LSIO's ``with-contenv`` applies ``UMASK`` only to services
whose directory is named ``svc-*``, so the web app honoured it while
``cwa-ingest-service``, ``metadata-change-detector`` and the other ``cwa-*``
services kept the container default of 022.

Every library-writing child goes through ``cwa-as-abc``, so that is where the
umask is applied. These tests run the real wrapper and read the umask the
command it launches actually gets, on both the non-root branch and the root
branch that chainloads ``s6-setuidgid``. The parent is pinned to 022, the state
every non-svc service starts in, so on the pre-fix wrapper the 027 cases fail.
"""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "root/usr/local/bin/cwa-as-abc"

# What a cwa-* service shell has before the fix: s6's default, not UMASK.
SERVICE_DEFAULT_UMASK = 0o022


def _env(**overrides):
    env = {k: v for k, v in os.environ.items() if k != "UMASK"}
    env.update(overrides)
    return env


def _run_child(tmp_path, env, parent_umask=SERVICE_DEFAULT_UMASK):
    """Run `umask` through the wrapper as this (non-root) user; return (umask, stderr)."""
    if os.geteuid() == 0:
        pytest.skip("must run unprivileged to exercise the direct-exec branch")
    out = tmp_path / "umask"
    result = subprocess.run(
        [str(HELPER), "/bin/sh", "-c", f"umask > {shlex.quote(str(out))}"],
        capture_output=True,
        text=True,
        env=env,
        umask=parent_umask,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"wrapper failed: rc={result.returncode} stderr={result.stderr!r}. "
        "Under set -e a failing umask here would crash-loop every service."
    )
    return out.read_text().strip(), result.stderr


def _umask_seen_by_child(tmp_path, env):
    return _run_child(tmp_path, env)[0]


@pytest.mark.parametrize("value, expected", [("027", "0027"), ("000", "0000"), ("0002", "0002")])
def test_child_gets_the_configured_umask(tmp_path, value, expected):
    assert _umask_seen_by_child(tmp_path, _env(UMASK=value)) == expected, (
        f"UMASK={value} did not reach the service's child process. New library "
        "folders keep 755 whatever the user configured (#2670)."
    )


@pytest.mark.parametrize("env", [{}, {"UMASK": ""}])
def test_unset_umask_keeps_the_inherited_one(tmp_path, env):
    """No UMASK means no opinion: a stricter umask from a rootless runtime stays."""
    assert _run_child(tmp_path, _env(**env), parent_umask=0o077)[0] == "0077"


@pytest.mark.parametrize("bad", ["abc", "999", "0x27"])
def test_invalid_umask_is_named_and_does_not_kill_the_service(tmp_path, bad):
    seen, stderr = _run_child(tmp_path, _env(UMASK=bad))
    assert seen == "0022", "an invalid UMASK must leave the inherited umask in place"
    assert f"UMASK='{bad}'" in stderr, (
        "the service log must say why UMASK had no effect, or the user sees 755 "
        f"folders with no clue; stderr was {stderr!r}"
    )


def test_umask_survives_the_privilege_drop(tmp_path):
    """The container path: root chainloads s6-setuidgid, which must inherit it.

    `id` and `s6-setuidgid` are stubbed so the root branch runs without root;
    the stub records the umask it was started with, which is what the dropped
    process inherits across exec.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    out = tmp_path / "umask"
    (stub_dir / "id").write_text("#!/bin/sh\necho 0\n")
    (stub_dir / "s6-setuidgid").write_text(f"#!/bin/sh\numask > {shlex.quote(str(out))}\n")
    for stub in stub_dir.iterdir():
        stub.chmod(0o755)

    result = subprocess.run(
        [str(HELPER), "python3", "/app/calibre-web-automated/scripts/ingest_processor.py"],
        capture_output=True,
        text=True,
        env=_env(UMASK="027", PATH=f"{stub_dir}:{os.environ['PATH']}"),
        umask=SERVICE_DEFAULT_UMASK,
        timeout=30,
    )

    assert result.returncode == 0, f"wrapper failed on the root path: {result.stderr!r}"
    assert out.read_text().strip() == "0027", (
        "as root the wrapper must set UMASK before dropping to abc, or the "
        "ingest processor still writes 755 folders (#2670)"
    )
