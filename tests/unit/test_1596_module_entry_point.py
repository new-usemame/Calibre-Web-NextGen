# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""``python -m cps`` is the container's entry point — pin it.

#1596 (by @chloeroform) moved ``main()`` into the package, added
``cps/__main__.py``, and switched both s6 units from
``cd /app/calibre-web-automated && python3 .../cps.py`` to a bare
``python3 -m cps``. Dropping the ``cd`` is only safe because the image
installs the package editable (Dockerfile STEP 6,
``pip install -e /app/calibre-web-automated``), so ``cps`` imports from any
cwd. The container's own working directory is ``/config``, not the app root,
so nothing else was holding that invocation up.

Dropping the chdir has a second consequence the units now guard with ``-P``:
``python -m`` puts the cwd at ``sys.path[0]``, and the cwd here is ``/config``
-- a volume the *user* mounts and writes. A stray ``/config/cps.py`` or
``/config/cps/`` would be imported in place of the application. Verified: both
forms hijack startup without ``-P`` and are ignored with it.

That makes the entry point a real contract with three halves and no coverage:

1. ``python -m cps`` must work **from an arbitrary cwd**. Testing it from the
   repo root would pass even if the editable install were the only thing
   making it work in production, so every test here runs from ``tmp_path``.
2. ``cps.py`` must keep working, because bare-metal and systemd installs (and
   ``AI_README.md``) still invoke it by path.
3. The cwd must not be able to supply the ``cps`` that gets imported.

Without a pin, a later edit can silently restore cwd-dependence or drop
``__main__.py`` and the failure only shows up as a container that will not
boot — and in ``cwa-init`` it would not even be visible, since that call
redirects to ``/dev/null``.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
S6 = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d"
SVC_RUN = S6 / "svc-calibre-web-automated/run"
INIT_RUN = S6 / "cwa-init/run"

# Long enough for a cold `import cps` (it builds the Flask app and logs
# ProxyFix setup on the way past) without hanging a CI job if --help ever
# stops short-circuiting.
HELP_TIMEOUT = 120


def _run_help(args, cwd):
    """Invoke the app's --help out-of-process and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, *args, "--help"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=HELP_TIMEOUT,
    )


def _poison(directory):
    """Drop a cps.py in `directory` that fails loudly if it ever gets imported.

    Stands in for whatever a user might leave in their mounted /config.
    """
    (directory / "cps.py").write_text(
        'raise SystemExit("shadowed: the cwd copy of cps was imported")\n'
    )


class TestModuleEntryPoint:
    """`python -m cps` — what the s6 longrun actually execs."""

    def test_module_is_executable_from_an_unrelated_cwd(self, tmp_path):
        """RED before #1596: 'No module named cps.__main__'.

        cwd is tmp_path on purpose. The s6 unit no longer chdirs into the app
        root, so resolving `cps` from somewhere else is the whole contract.
        """
        result = _run_help(["-P", "-m", "cps"], cwd=tmp_path)

        assert result.returncode == 0, (
            "`python -P -m cps --help` must succeed from an arbitrary cwd; the s6 "
            f"longrun runs exactly that with cwd=/config.\nstderr:\n{result.stderr}"
        )
        assert "usage:" in result.stdout.lower(), (
            f"expected argparse usage on stdout, got:\n{result.stdout!r}"
        )

    def test_help_names_the_program_not_the_dunder_file(self, tmp_path):
        """RED before the prog= fix: 'usage: __main__.py [-h] ...'.

        argparse falls back to basename(sys.argv[0]), which under `-m` is the
        package's __main__.py. #1596 dropped prog='cps.py' as no longer true
        without replacing it, so the invocation the PR *promotes* printed the
        least useful name of the three.
        """
        usage = _run_help(["-P", "-m", "cps"], cwd=tmp_path).stdout.splitlines()[0]

        assert "__main__" not in usage, (
            f"--help must not call the program __main__.py; got: {usage!r}"
        )
        assert "cps" in usage, f"--help should name the program cps; got: {usage!r}"

    def test_legacy_script_entry_point_still_works(self, tmp_path):
        """cps.py is retained for bare-metal/systemd installs — keep it working.

        Run from tmp_path too: cps.py anchors sys.path to its own directory, so
        it must not need the caller to be in the app root either.
        """
        result = _run_help([str(REPO_ROOT / "cps.py")], cwd=tmp_path)

        assert result.returncode == 0, (
            "cps.py must keep working; systemd units and AI_README.md invoke it "
            f"by path.\nstderr:\n{result.stderr}"
        )
        assert "usage:" in result.stdout.lower()


class TestCwdCannotShadowTheApplication:
    """/config is a user-mounted volume and it is the service's cwd."""

    def test_minus_P_ignores_a_cps_py_sitting_in_the_cwd(self, tmp_path):
        """The guard that lets the units run without chdir'ing into the app root."""
        _poison(tmp_path)

        result = _run_help(["-P", "-m", "cps"], cwd=tmp_path)

        assert result.returncode == 0, (
            "-P must keep cwd off sys.path so a stray /config/cps.py cannot be "
            f"imported in place of the app.\nstderr:\n{result.stderr}"
        )
        assert "shadowed" not in result.stderr.lower()

    def test_without_minus_P_the_cwd_copy_wins(self, tmp_path):
        """Control: proves the -P above is load-bearing, not decoration.

        If this ever stops shadowing, the guard is being kept for a hazard that
        no longer exists and the test above has quietly stopped proving anything.
        """
        _poison(tmp_path)

        result = _run_help(["-m", "cps"], cwd=tmp_path)

        assert result.returncode != 0 and "shadowed" in result.stderr.lower(), (
            "expected the cwd copy of cps to win without -P; if it no longer "
            f"does, revisit why the units pass -P.\nstderr:\n{result.stderr}"
        )


class TestConsoleHidingStaysOutOfMain:
    """`cps` (the console script) must not hide a Windows user's terminal.

    pyproject exposes ``cps = "cps.main:main"``, so anything main() does on
    import-and-call happens to someone who just typed ``cps`` at a prompt.
    Hiding their console loses the server output and their Ctrl-C while the
    process keeps running. Only the two *script* entry points hide it, which is
    what cps.py did before #1596 moved the call into main().
    """

    def _main_fn(self):
        tree = ast.parse((REPO_ROOT / "cps" / "main.py").read_text())
        return next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )

    def test_main_does_not_hide_the_console(self):
        calls = [
            n.func.id
            for n in ast.walk(self._main_fn())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]

        assert "hide_console_windows" not in calls, (
            "main() is the `cps` console-script entry point; hiding the console "
            "there hits users who invoked it from a terminal on purpose."
        )

    @pytest.mark.parametrize(
        "entry", [REPO_ROOT / "cps.py", REPO_ROOT / "cps" / "__main__.py"],
        ids=["cps.py", "cps/__main__.py"],
    )
    def test_script_entry_points_still_hide_it(self, entry):
        body = entry.read_text()

        assert "hide_console_windows()" in body, (
            f"{entry.name} must keep the Windows console-hiding behaviour that "
            "cps.py had before the entry point moved."
        )


class TestS6UnitsUseTheModule:
    """Pin the boot command itself, so a revert cannot be silent."""

    @pytest.mark.parametrize("unit", [SVC_RUN, INIT_RUN], ids=["svc", "cwa-init"])
    def test_unit_invokes_the_module_with_safe_path(self, unit):
        body = unit.read_text()

        assert "python3 -P -m cps" in body, (
            f"{unit.name} must start the app as a module with -P; without it the "
            f"unit's cwd (/config, user-writable) lands on sys.path[0].\nfound:\n{body}"
        )

    @pytest.mark.parametrize("unit", [SVC_RUN, INIT_RUN], ids=["svc", "cwa-init"])
    def test_unit_does_not_reintroduce_the_script_path(self, unit):
        """The old form only worked because the unit chdir'd first."""
        body = unit.read_text()

        assert "calibre-web-automated/cps.py" not in body, (
            f"{unit.name} still execs cps.py by path; that pairing needs the "
            "`cd /app/calibre-web-automated` this change removed."
        )

    def test_main_module_delegates_to_cps_main(self):
        """__main__.py stays a thin shim — the logic belongs in cps/main.py."""
        body = (REPO_ROOT / "cps" / "__main__.py").read_text()

        assert "from cps.main import" in body, (
            "__main__.py should import the entry point rather than reimplement it"
        )
        assert "main()" in body
