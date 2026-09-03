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

Dropping the chdir has a second consequence the units guard in three parts.
``-P`` prevents ``python -m`` from putting the user-mounted ``/config`` cwd at
``sys.path[0]``; removing ``PYTHONPATH`` prevents an injected environment path
from outranking the editable install; and ``PYTHONNOUSERSITE=1`` excludes user
site ``.pth`` and ``usercustomize`` hooks, including a user base redirected
under ``/config``. The image-controlled system site remains enabled because its
editable ``.pth`` resolves the application from ``/app/calibre-web-automated``.
Its ``.pth`` and any system ``sitecustomize`` are trusted image contents, not
covered as runtime input. Verified: cwd and hostile ``PYTHONPATH`` copies both
hijack the corresponding weaker command and are ignored by their control.

That makes the entry point a real contract with three halves and no coverage:

1. ``python -m cps`` must work **from an arbitrary cwd**. Testing it from the
   repo root would pass even if the editable install were the only thing
   making it work in production, so every test here runs from ``tmp_path``.
2. ``cps.py`` must keep working, because bare-metal and systemd installs (and
   ``AI_README.md``) still invoke it by path.
3. Neither the cwd nor runtime-controlled Python path/user-site inputs may
   supply the ``cps`` that gets imported.

Without a pin, a later edit can silently restore cwd-dependence or drop
``__main__.py`` and the failure only shows up as a container that will not
boot — and in ``cwa-init`` it would not even be visible, since that call
redirects to ``/dev/null``.
"""

import ast
import os
import shutil
import subprocess
import sys
import venv
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


_SHADOW = 'raise SystemExit("shadowed: the cwd copy of cps was imported")\n'

# Both shapes a user can leave in their mounted /config. A module and a package
# take different paths through the import system, and the package form is the
# one a half-extracted archive or a stray checkout actually produces -- so
# pinning only `cps.py` would leave the more likely accident uncovered.
POISON_FORMS = ("module", "package")


def _poison(directory, form="module"):
    """Plant a `cps` in `directory` that fails loudly if it ever gets imported.

    Stands in for whatever a user might leave in their mounted /config.
    """
    if form == "module":
        (directory / "cps.py").write_text(_SHADOW)
    elif form == "package":
        package = directory / "cps"
        package.mkdir()
        (package / "__init__.py").write_text(_SHADOW)
        # A package is only a viable hijack if `-m` can find an entry point,
        # so give it the same one the real package has.
        (package / "__main__.py").write_text(_SHADOW)
    else:  # pragma: no cover - guards a typo in a parametrize list
        raise ValueError(f"unknown poison form: {form}")


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

    @pytest.mark.parametrize("form", POISON_FORMS)
    def test_minus_P_ignores_a_cps_sitting_in_the_cwd(self, tmp_path, form):
        """The guard that lets the units run without chdir'ing into the app root."""
        _poison(tmp_path, form)

        result = _run_help(["-P", "-m", "cps"], cwd=tmp_path)

        assert result.returncode == 0, (
            f"-P must keep cwd off sys.path so a stray /config/cps ({form} form) "
            f"cannot be imported in place of the app.\nstderr:\n{result.stderr}"
        )
        assert "shadowed" not in result.stderr.lower()

    @pytest.mark.parametrize("form", POISON_FORMS)
    def test_without_minus_P_the_cwd_copy_wins(self, tmp_path, form):
        """Control: proves the -P above is load-bearing, not decoration.

        If this ever stops shadowing, the guard is being kept for a hazard that
        no longer exists and the test above has quietly stopped proving anything.
        """
        _poison(tmp_path, form)

        result = _run_help(["-m", "cps"], cwd=tmp_path)

        assert result.returncode != 0 and "shadowed" in result.stderr.lower(), (
            f"expected the cwd copy of cps ({form} form) to win without -P; if "
            f"it no longer does, revisit why the units pass -P.\n"
            f"stderr:\n{result.stderr}"
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

        command = "/usr/bin/env -u PYTHONPATH PYTHONNOUSERSITE=1 python3 -P -m cps"
        assert command in body, (
            f"{unit.name} must start the app with a clean import environment: "
            "-P excludes the /config cwd, -u excludes an injected PYTHONPATH, "
            "and PYTHONNOUSERSITE excludes /config-derived user site hooks.\n"
            f"found:\n{body}"
        )

    def test_hostile_pythonpath_cannot_shadow_the_editable_application(self, tmp_path):
        """RED before F-9ca4a5: ``-P`` alone still honors PYTHONPATH.

        The clean lookup is the image's editable application install (under
        ``/app/calibre-web-automated`` in Docker).  The attacked lookup starts
        with a hostile ``PYTHONPATH`` and runs through the exact ``env`` policy
        pinned in both units above; it must resolve to the same trusted module.
        ``find_spec`` avoids importing the Flask application and its optional
        runtime dependencies just to inspect the selected path.
        """
        # Reproduce the image's import topology without importing the Flask
        # application: an image-controlled site-packages .pth points at the
        # /app-shaped application root, while deployment input supplies a
        # competing PYTHONPATH. This avoids accidentally testing whichever
        # editable checkout happens to be installed in the developer's Python.
        app_root = tmp_path / "app" / "calibre-web-automated"
        app_package = app_root / "cps"
        app_package.mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "cps" / "__init__.py", app_package / "__init__.py")

        venv_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=False).create(venv_dir)
        venv_python = venv_dir / "bin" / "python"
        site_packages = Path(
            subprocess.run(
                [
                    venv_python,
                    "-P",
                    "-c",
                    "import site; print(site.getsitepackages()[0])",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        (site_packages / "cwa-editable.pth").write_text(f"{app_root}\n")

        poison = tmp_path / "poison"
        poison.mkdir()
        (poison / "cps.py").write_text("# hostile PYTHONPATH module\n")
        lookup = (
            "import importlib.util; "
            "spec = importlib.util.find_spec('cps'); "
            "print(spec.origin if spec else 'NOT_FOUND')"
        )
        attacked_env = {**os.environ, "PYTHONPATH": str(poison)}
        clean_env = dict(attacked_env)
        clean_env.pop("PYTHONPATH")
        clean_env["PYTHONNOUSERSITE"] = "1"

        control = subprocess.run(
            [venv_python, "-P", "-c", lookup],
            cwd=tmp_path,
            env=attacked_env,
            capture_output=True,
            text=True,
            check=True,
        )
        trusted = subprocess.run(
            [venv_python, "-P", "-c", lookup],
            cwd=tmp_path,
            env=clean_env,
            capture_output=True,
            text=True,
            check=True,
        )
        hardened = subprocess.run(
            [
                shutil.which("env") or "/usr/bin/env",
                "-u",
                "PYTHONPATH",
                "PYTHONNOUSERSITE=1",
                venv_python,
                "-P",
                "-c",
                lookup,
            ],
            cwd=tmp_path,
            env=attacked_env,
            capture_output=True,
            text=True,
            check=True,
        )

        assert Path(control.stdout.strip()).is_relative_to(poison)
        assert Path(trusted.stdout.strip()).is_relative_to(app_root)
        assert hardened.stdout == trusted.stdout
        assert Path(hardened.stdout.strip()).is_relative_to(app_root)

        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        assert "-e /app/calibre-web-automated" in dockerfile

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


class TestFailedFirstRunLeavesNoPhantomDatabase:
    """cwa-init must not invent an app.db the initializer failed to create.

    `sqlite3 <path>` creates the file it is pointed at. Running it after a
    failed hardened `python3 -P -m cps -d` therefore replaces "no database"
    with a 0-byte one, and cps.ub.init_db() branches on os.path.exists(): the
    existing-file branch migrates, and only the fresh branch calls
    create_admin_user()/create_anonymous_user(). The user gets a schema with no
    admin account, no login, and no retry -- the enclosing
    `if [[ ! -f /config/app.db ]]` never fires again.
    """

    # Every sqlite3 call, not just the first-run one. The unconditional
    # "ensure correct binary paths" call further down runs on EVERY start, so
    # it is the one a failed first run actually reaches; guarding only the
    # first-run block would leave the phantom database exactly as reachable.
    GUARD = "if [[ -f /config/app.db ]]"

    def test_every_sqlite3_call_is_guarded_on_the_database_existing(self):
        lines = INIT_RUN.read_text().splitlines()

        calls = [i for i, line in enumerate(lines) if "sqlite3 /config/app.db" in line]
        assert calls, "expected cwa-init to touch app.db with sqlite3 at all"

        unguarded = []
        for index in calls:
            # Walk back to the nearest enclosing app.db conditional.
            preceding = [
                line.strip()
                for line in lines[:index]
                if "/config/app.db ]]" in line
            ]
            if not preceding or not preceding[-1].startswith(self.GUARD):
                unguarded.append(index + 1)

        assert not unguarded, (
            f"sqlite3 calls on lines {unguarded} of {INIT_RUN.name} are not "
            f"inside an `{self.GUARD}` guard. sqlite3 creates the file it is "
            "pointed at, so an unguarded call turns a failed first run into a "
            "phantom 0-byte app.db — and cps.ub.init_db() then takes its "
            "existing-database branch and never creates the admin user."
        )

    def test_sqlite3_status_is_captured_not_reread(self):
        """`$?` after an `if [[ ]]` reports the test, not the command."""
        body = INIT_RUN.read_text()

        assert "sqlite_rc=$?" in body, (
            "capture sqlite3's exit status immediately; the old "
            "`elif [[ $? > 0 ]]` re-read $? from the preceding [[ ]] and "
            "compared it as a string"
        )
        # Comments are stripped: the fix's own comment quotes the old form to
        # explain it, and a test that cannot tell code from prose would fail on
        # its own documentation.
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        assert "$? > 0" not in code, (
            "the string-comparing rc check is back (shellcheck SC2071): `>` "
            "inside [[ ]] compares strings, and $? there re-reads the "
            "preceding test rather than the command"
        )

    def test_phantom_database_would_skip_admin_creation(self):
        """Pins WHY the guard matters, so nobody 'simplifies' it away.

        If init_db ever creates the admin user on both branches, this test
        fails and the guard above can be reconsidered on purpose rather than
        by accident.
        """
        source = (REPO_ROOT / "cps" / "ub.py").read_text()
        tree = ast.parse(source)

        init_db = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "init_db"
        )
        branch = next(
            node
            for node in init_db.body
            if isinstance(node, ast.If)
            and "exists" in ast.dump(node.test)
        )

        def calls(statements):
            return {
                n.func.id
                for stmt in statements
                for n in ast.walk(stmt)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }

        assert "create_admin_user" in calls(branch.orelse), (
            "expected the fresh-database branch to create the admin user"
        )
        assert "create_admin_user" not in calls(branch.body), (
            "init_db now creates an admin user even when app.db already "
            "exists — re-evaluate the cwa-init phantom-database guard"
        )
