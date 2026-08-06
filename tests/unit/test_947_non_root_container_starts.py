# SPDX-License-Identifier: GPL-3.0-or-later
"""Starting the container as a non-root user must not crash-loop it (#947).

@KucharczykL ran the published image under rootless Podman as an arbitrary
non-root uid and found every long-running service dying on::

    s6-applyuidgid: fatal: unable to set supplementary group list:
                    Operation not permitted

Mechanism: on the non-root path LSIO's ``init-adduser`` is skipped, so ``abc``
keeps its build-time 911:1001, and every service ended in an unconditional
``s6-setuidgid abc <cmd>``. Dropping from one unprivileged uid to a *different*
one needs ``setgroups()``, which an unprivileged process may not call.

The failure is quiet, which is the dangerous part: the supervisor keeps
restarting the services, so the container reports ``Up`` with no web server
behind it.

These tests pin the fix: every privilege drop goes through ``cwa-as-abc``, which
drops only when it is root, and both ownership-fixing units check before they
try. The static scan is the one that fails on the pre-fix tree; the execution
tests prove the helper actually behaves, in both branches, rather than merely
existing.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
S6_ROOT = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d"
HELPER = REPO_ROOT / "root/usr/local/bin/cwa-as-abc"

# A privilege drop, ignoring occurrences inside a comment.
UNGUARDED = re.compile(r"^\s*[^#\n]*\bs6-setuidgid\s+abc\b")


def _run_scripts():
    scripts = sorted(S6_ROOT.glob("*/run"))
    assert scripts, f"no s6 run scripts found under {S6_ROOT}"
    return scripts


def test_no_service_drops_privileges_unconditionally():
    """The pre-fix state: nine services ending in a bare `s6-setuidgid abc`."""
    offenders = []
    for script in _run_scripts():
        for lineno, line in enumerate(script.read_text().splitlines(), 1):
            if UNGUARDED.match(line):
                offenders.append(f"{script.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "These call sites drop to `abc` without checking that we are root. "
        "Started as a non-root user they fail with EPERM and the service "
        "crash-loops while the container still reports Up (#947). "
        "Use `cwa-as-abc` instead:\n  " + "\n  ".join(offenders)
    )


def test_every_service_that_needs_the_app_user_uses_the_helper():
    """Guard against the fix being 'applied' by deleting the drop entirely."""
    users = [s for s in _run_scripts() if "cwa-as-abc" in s.read_text()]
    assert len(users) >= 7, (
        "Expected the privilege drop to survive as `cwa-as-abc` in the services "
        f"that had it; only {len(users)} reference the helper."
    )


def test_helper_ships_executable():
    """`cp -R root/* /` preserves mode; a 644 helper breaks every service."""
    assert HELPER.exists(), f"{HELPER} is missing"
    mode = HELPER.stat().st_mode
    assert mode & stat.S_IXUSR and mode & stat.S_IXGRP and mode & stat.S_IXOTH, (
        f"{HELPER} must be executable by all (is {stat.filemode(mode)}). "
        "The image copies this tree verbatim, so a non-executable helper "
        "means every service fails to start."
    )


def test_helper_runs_the_command_directly_when_not_root(tmp_path):
    """The #947 path. Runs as the (non-root) test user, so this is the real thing."""
    if os.geteuid() == 0:
        pytest.skip("test must run unprivileged to exercise the non-root branch")

    marker = tmp_path / "ran"
    result = subprocess.run(
        [str(HELPER), "/bin/sh", "-c", f"id -u > {marker}"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"helper failed as a non-root user: rc={result.returncode} "
        f"stderr={result.stderr!r}. This is exactly the #947 crash."
    )
    assert marker.exists(), "helper did not run the command it was given"
    assert marker.read_text().strip() == str(os.geteuid()), (
        "command should run as the current user, not some other uid"
    )


def test_helper_still_drops_to_abc_when_root(tmp_path):
    """The normal path must be unchanged: as root, still `s6-setuidgid abc`.

    Stubs `id` and `s6-setuidgid` on PATH so the root branch is exercised
    without the test itself needing root.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()

    (stub_dir / "id").write_text("#!/bin/sh\necho 0\n")
    out = tmp_path / "dropped"
    (stub_dir / "s6-setuidgid").write_text(f'#!/bin/sh\necho "$@" > {out}\n')
    for stub in stub_dir.iterdir():
        stub.chmod(0o755)

    env = dict(os.environ, PATH=f"{stub_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        [str(HELPER), "python3", "/app/calibre-web-automated/cps.py"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, f"helper failed on the root path: {result.stderr!r}"
    assert out.exists(), "as root the helper must chainload s6-setuidgid"
    assert out.read_text().strip() == "abc python3 /app/calibre-web-automated/cps.py", (
        "the root path must still drop to `abc` and pass the command through unchanged"
    )


@pytest.mark.parametrize("unit", ["cwa-init", "cwa-chown-library-migration"])
def test_ownership_units_check_before_chowning(unit):
    """Both chown paths fail per-file as non-root; each must check first."""
    body = (S6_ROOT / unit / "run").read_text()
    assert 'id -u' in body, (
        f"{unit}/run changes ownership but never checks whether it is root. "
        "As a non-root user that is one EPERM per file (#947)."
    )


def _run_ownership_pass(tmp_path, uid):
    """Drive the real scripts/set_ownership.sh with a chown that takes notes."""
    app_root = tmp_path / "app"
    config_root = tmp_path / "config"
    for d in (app_root, config_root):
        d.mkdir(parents=True, exist_ok=True)
    (app_root / "dirs.json").write_text('{"calibre_library_dir": "%s"}' % config_root)

    chown_log = tmp_path / "chown.log"
    chown_stub = tmp_path / "chown-stub"
    chown_stub.write_text('#!/bin/sh\necho "$@" >> "$CWA_TEST_CHOWN_LOG"\nexit 0\n')
    chown_stub.chmod(0o755)

    env = dict(
        os.environ,
        CWA_APP_ROOT=str(app_root),
        CWA_CONFIG_ROOT=str(config_root),
        CWA_DIRS_JSON=str(app_root / "dirs.json"),
        CWA_OWNER_USER=str(os.getuid()),
        CWA_CHOWN=str(chown_stub),
        CWA_TEST_CHOWN_LOG=str(chown_log),
        CWA_UID=str(uid),
    )
    env.pop("NETWORK_SHARE_MODE", None)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "set_ownership.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    calls = chown_log.read_text().splitlines() if chown_log.exists() else []
    return result, calls


def test_ownership_pass_skips_itself_entirely_when_not_root(tmp_path):
    """The noisiest half of #947: one EPERM line per directory, all unavoidable.

    Live evidence from the fixed image: the guarded units went quiet but this
    pass kept going, because it lives in scripts/set_ownership.sh rather than in
    the s6 unit -- which the source-level check above does not reach.
    """
    result, calls = _run_ownership_pass(tmp_path, uid=1000)

    assert result.returncode == 0, f"the pass must not fail the boot: {result.stderr!r}"
    assert calls == [], (
        "set_ownership.sh tried to chown as a non-root user. Every call fails "
        f"with EPERM and prints a line saying so: {calls}"
    )
    assert "not root" in result.stdout, (
        "skipping silently is its own problem -- say once why nothing was "
        f"chowned. stdout was: {result.stdout!r}"
    )


def test_ownership_pass_still_walks_when_root(tmp_path):
    """The guard must not disable the pass on the path everyone actually uses."""
    result, calls = _run_ownership_pass(tmp_path, uid=0)

    assert result.returncode == 0
    assert calls, "as root the ownership pass must still walk its directories"


def test_non_root_run_does_not_mark_the_chown_migration_done():
    """A non-root start must not poison the sentinel for a later rootful one."""
    body = (S6_ROOT / "cwa-chown-library-migration/run").read_text()
    guard = body.index('id -u')
    touch = body.index('touch "$MARKER"')
    assert guard < touch, "the root check must come before the sentinel is written"

    between = body[guard:touch]
    assert "exit 0" in between, (
        "the non-root branch must exit before `touch $MARKER`. Writing the "
        "sentinel on a run that could not chown anything means a later rootful "
        "start skips the migration for good (#947)."
    )
