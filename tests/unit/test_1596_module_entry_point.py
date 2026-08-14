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

That makes the entry point a real contract with two halves and no coverage:

1. ``python -m cps`` must work **from an arbitrary cwd**. Testing it from the
   repo root would pass even if the editable install were the only thing
   making it work in production, so every test here runs from ``tmp_path``.
2. ``cps.py`` must keep working, because bare-metal and systemd installs (and
   ``AI_README.md``) still invoke it by path.

Without a pin, a later edit can silently restore cwd-dependence or drop
``__main__.py`` and the failure only shows up as a container that will not
boot — and in ``cwa-init`` it would not even be visible, since that call
redirects to ``/dev/null``.
"""

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


class TestModuleEntryPoint:
    """`python -m cps` — what the s6 longrun actually execs."""

    def test_module_is_executable_from_an_unrelated_cwd(self, tmp_path):
        """RED before #1596: 'No module named cps.__main__'.

        cwd is tmp_path on purpose. The s6 unit no longer chdirs into the app
        root, so resolving `cps` from somewhere else is the whole contract.
        """
        result = _run_help(["-m", "cps"], cwd=tmp_path)

        assert result.returncode == 0, (
            "`python -m cps --help` must succeed from an arbitrary cwd; the s6 "
            f"longrun runs it with cwd=/config.\nstderr:\n{result.stderr}"
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
        usage = _run_help(["-m", "cps"], cwd=tmp_path).stdout.splitlines()[0]

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


class TestS6UnitsUseTheModule:
    """Pin the boot command itself, so a revert cannot be silent."""

    @pytest.mark.parametrize("unit", [SVC_RUN, INIT_RUN], ids=["svc", "cwa-init"])
    def test_unit_invokes_the_module(self, unit):
        body = unit.read_text()

        assert "python3 -m cps" in body, (
            f"{unit.name} must start the app as a module; found:\n{body}"
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

        assert "from cps.main import main" in body
        assert "main()" in body
