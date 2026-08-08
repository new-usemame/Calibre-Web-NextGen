# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #1462 — ``/app`` hardcoded into ``scripts/``.

Reported by @Thovi98, who packages Calibre-Web-NextGen for YunoHost and installs
it to ``/var/www/calibreweb-nextgen/build`` rather than the container's
``/app/calibre-web-automated``. The install dies on the very first script it
runs::

    /var/www/calibreweb-nextgen/build/venv/bin/python3 auto_library.py
    [cwa-auto-library] No app.db found in /config, copying from
    /app/calibre-web-automated/empty_library/app.db
    FileNotFoundError: [Errno 2] No such file or directory:
    '/app/calibre-web-automated/empty_library/app.db'

The file exists — at ``<checkout>/empty_library/app.db``. ``scripts/`` just
refuses to look there, because the app root is written out as a literal in ~29
places instead of being derived from where the code actually lives.

The conventions to fix it were already here and already honoured in a couple of
places (``CWA_APP_ROOT`` in ``scripts/set_ownership.sh``, ``CALIBRE_DBPATH`` in
``scripts/cover_enforcer.py`` and ``scripts/ingest_processor.py``); they just
were not applied consistently. ``scripts/app_paths.py`` makes them the single
source of truth and every script resolves through it.

These tests pin the contract, not one call site, so re-introducing a literal in
any script fails the suite rather than waiting for the next packager to hit it.

Follows @chloeroform's ``cps/`` de-hardcoding in #1438.
"""

import importlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
APP_ROOT_LITERAL = "/app/calibre-web-automated"

sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def app_paths(monkeypatch):
    """Import ``scripts/app_paths.py`` with a clean environment.

    Every resolver reads os.environ at call time, so the module is imported
    once and the tests vary the environment around it.
    """
    for var in (
        "CWA_APP_ROOT",
        "CALIBRE_DBPATH",
        "CWA_DIRS_JSON",
        "CWA_APP_DB_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    module = importlib.import_module("app_paths")
    return importlib.reload(module)


# --------------------------------------------------------------------------
# The guard: no script may write the app root out as a literal again.
# --------------------------------------------------------------------------


def _python_scripts():
    return sorted(
        p for p in SCRIPTS_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


def test_no_python_script_hardcodes_the_app_root():
    """#1462 — the literal is what broke @Thovi98's bare-metal install."""
    offenders = {}
    for path in _python_scripts():
        hits = [
            f"{n}: {line.strip()}"
            for n, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if APP_ROOT_LITERAL in line
        ]
        if hits:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hits

    assert not offenders, (
        "scripts/ must resolve the app root through app_paths, not a literal.\n"
        + "\n".join(
            f"{f}\n  " + "\n  ".join(h) for f, h in sorted(offenders.items())
        )
    )


def test_app_paths_module_exists_and_is_importable_without_cps():
    """It must not drag in the Flask stack — auto_library runs before cps does.

    Executed in a clean subprocess with an import hook that raises on any
    attempt to load ``cps`` (directly or transitively), rather than grepping the
    source: a regex cannot see ``importlib.import_module("cps")`` or an import
    reached through another module.
    """
    assert (SCRIPTS_DIR / "app_paths.py").is_file()
    probe = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'cps' or name.startswith('cps.'):\n"
        "            raise AssertionError('app_paths imported ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "import app_paths\n"
        "app_paths.app_root(); app_paths.config_dir(); app_paths.app_db_path()\n"
        "app_paths.dirs_json(); app_paths.empty_library_file('app.db')\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(SCRIPTS_DIR),
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SCRIPTS_DIR)},
    )
    assert result.returncode == 0 and "OK" in result.stdout, (
        f"app_paths must import and resolve without cps.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# --------------------------------------------------------------------------
# The behaviour: resolve relative to the checkout, honour the env overrides.
# --------------------------------------------------------------------------


def test_app_root_resolves_to_this_checkout_not_slash_app(app_paths):
    """The reporter's core symptom: the app root follows the code."""
    assert app_paths.app_root() == REPO_ROOT
    assert not str(app_paths.app_root()).startswith("/app/")


def test_empty_library_seed_files_exist_under_the_resolved_root(app_paths):
    """The exact file the traceback died on — findable from any checkout path."""
    seed = app_paths.empty_library_file("app.db")
    assert seed == REPO_ROOT / "empty_library" / "app.db"
    assert seed.is_file(), (
        "auto_library.check_for_app_db() copies this file; #1462 was it "
        "resolving to /app/... and raising FileNotFoundError"
    )
    assert app_paths.empty_library_file("metadata.db").is_file()


def test_app_root_honours_cwa_app_root_override(app_paths, monkeypatch, tmp_path):
    """Packagers who relocate the tree get an explicit knob."""
    monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))
    assert app_paths.app_root() == tmp_path
    assert app_paths.dirs_json() == tmp_path / "dirs.json"
    assert app_paths.empty_library_file("app.db") == tmp_path / "empty_library" / "app.db"


def test_dirs_json_defaults_under_app_root_and_honours_its_override(
    app_paths, monkeypatch, tmp_path
):
    assert app_paths.dirs_json() == REPO_ROOT / "dirs.json"
    override = tmp_path / "elsewhere" / "dirs.json"
    monkeypatch.setenv("CWA_DIRS_JSON", str(override))
    assert app_paths.dirs_json() == override


def test_config_dir_keeps_the_slash_config_default(app_paths):
    """#1162 contract: the container sets CALIBRE_DBPATH=/config as a Docker ENV.

    The fallback must stay ``/config`` — the value scripts/ already used — so
    de-hardcoding the app root cannot quietly move the container's databases.
    """
    assert app_paths.config_dir() == pathlib.Path("/config")


def test_config_dir_honours_calibre_dbpath(app_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    assert app_paths.config_dir() == tmp_path
    assert app_paths.app_db_path() == tmp_path / "app.db"


def test_app_db_path_matches_the_ingest_processor_resolver(app_paths, monkeypatch, tmp_path):
    """SSOT: one app.db resolver, including its two odd legacy branches.

    ``scripts/ingest_processor.get_app_db_path()`` already honoured
    ``CWA_APP_DB_PATH`` and a ``CALIBRE_DBPATH`` that points at a ``.db`` file.
    Those semantics move into app_paths rather than being duplicated.
    """
    explicit = tmp_path / "somewhere" / "custom.db"
    monkeypatch.setenv("CWA_APP_DB_PATH", str(explicit))
    assert app_paths.app_db_path() == explicit

    monkeypatch.delenv("CWA_APP_DB_PATH")
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "other.db"))
    assert app_paths.app_db_path() == tmp_path / "app.db"

    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "app.db"))
    assert app_paths.app_db_path() == tmp_path / "app.db"


def test_ingest_processor_delegates_to_app_paths(app_paths, monkeypatch, tmp_path):
    """The duplicated resolver is gone, not merely shadowed.

    Calls the real function across an environment matrix and compares it to the
    central resolver. A restored private copy would agree on the easy cases and
    diverge on the awkward ones, which is exactly what this walks.
    """
    ingest = importlib.import_module("ingest_processor")

    cases = [
        {},
        {"CALIBRE_DBPATH": str(tmp_path)},
        {"CALIBRE_DBPATH": str(tmp_path / "other.db")},
        {"CALIBRE_DBPATH": str(tmp_path / "app.db")},
        {"CWA_APP_DB_PATH": str(tmp_path / "explicit" / "custom.db")},
        {
            "CWA_APP_DB_PATH": str(tmp_path / "wins.db"),
            "CALIBRE_DBPATH": str(tmp_path / "ignored"),
        },
    ]
    for env in cases:
        for var in ("CALIBRE_DBPATH", "CWA_APP_DB_PATH"):
            monkeypatch.delenv(var, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert ingest.get_app_db_path() == str(app_paths.app_db_path()), (
            f"ingest_processor diverged from app_paths under {env}"
        )


def test_auto_library_reads_the_library_dir_from_dirs_json(
    app_paths, monkeypatch, tmp_path
):
    """The second half of #1462, found by running the reporter's flow.

    Fixing only the seed-database path moves the crash one line down:
    ``make_new_library()`` writes ``metadata.db`` into ``self.library_dir``,
    which was the container's ``/calibre-library`` mount point regardless of
    what ``dirs.json`` said. On bare metal that directory does not exist and
    the install still dies::

        FileNotFoundError: [Errno 2] No such file or directory:
        '/calibre-library/metadata.db'

    ``dirs.json`` is already the configuration of record here — the same class
    writes ``calibre_library_dir`` back to it in ``set_library_location()``.
    """
    library = tmp_path / "books"
    library.mkdir()
    dirs = tmp_path / "dirs.json"
    dirs.write_text(
        json.dumps(
            {
                "ingest_folder": str(tmp_path / "ingest"),
                "calibre_library_dir": str(library),
                "tmp_conversion_dir": str(tmp_path / "tmp"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs))
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "config"))

    library_paths = importlib.reload(importlib.import_module("library_paths"))
    assert library_paths.get_calibre_library_dir() == str(library)

    auto_library = importlib.reload(importlib.import_module("auto_library"))
    lib = auto_library.AutoLibrary()
    assert lib.library_dir == str(library), (
        "AutoLibrary must take the library root from dirs.json, not assume "
        "the container's /calibre-library mount"
    )
    # app.db must follow CALIBRE_DBPATH too, through the same resolver.
    assert lib.DEFAULT_APPDB_PATH == str(app_paths.app_db_path())
    assert lib.empty_appdb == str(REPO_ROOT / "empty_library" / "app.db")


def test_auto_library_creates_a_missing_library_dir(app_paths, monkeypatch, tmp_path):
    """The rest of #1462, found by running the reporter's DEFAULT configuration.

    The first pass at this fix was verified against a hand-written dirs.json and
    looked complete. Re-run with the dirs.json the project actually ships —
    which still says ``/calibre-library`` — and the install died anyway, in
    ``make_new_library()``::

        FileNotFoundError: [Errno 2] No such file or directory:
        '/calibre-library/metadata.db'

    In the container that path is a bind mount and always exists. Off Docker it
    does not, and nothing created it.
    """
    library = tmp_path / "not-yet-created" / "books"
    dirs = tmp_path / "dirs.json"
    dirs.write_text(json.dumps({"calibre_library_dir": str(library)}), encoding="utf-8")
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs))
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "config"))
    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")

    auto_library = importlib.reload(importlib.import_module("auto_library"))
    lib = auto_library.AutoLibrary()
    assert not library.exists()

    lib.make_new_library()

    assert (library / "metadata.db").is_file(), (
        "make_new_library must create the configured library dir before seeding it"
    )


def test_auto_library_seeds_app_db_into_a_missing_config_dir(app_paths, monkeypatch, tmp_path):
    """Same root cause, the other database. CALIBRE_DBPATH may not exist yet."""
    config = tmp_path / "fresh-config"
    dirs = tmp_path / "dirs.json"
    dirs.write_text(json.dumps({"calibre_library_dir": str(tmp_path / "books")}), encoding="utf-8")
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs))
    monkeypatch.setenv("CALIBRE_DBPATH", str(config))
    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")

    auto_library = importlib.reload(importlib.import_module("auto_library"))
    lib = auto_library.AutoLibrary()
    assert not config.exists()

    lib.check_for_app_db()

    assert (config / "app.db").is_file()
    assert lib.app_db == str(config / "app.db")


def test_auto_library_reports_an_unwritable_library_dir_instead_of_a_traceback(
    app_paths, monkeypatch, tmp_path, capsys
):
    """A path we cannot create must name the setting to edit, not raise.

    This is the shipped-dirs.json case on a non-root source install: the user
    never chose ``/calibre-library``, so a bare FileNotFoundError about it is
    not something they can act on.
    """
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)  # no write permission
    dirs = tmp_path / "dirs.json"
    dirs.write_text(
        json.dumps({"calibre_library_dir": str(blocked / "library")}), encoding="utf-8"
    )
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs))
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "config"))

    auto_library = importlib.reload(importlib.import_module("auto_library"))
    lib = auto_library.AutoLibrary()
    try:
        with pytest.raises(SystemExit) as excinfo:
            lib.make_new_library()
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "could not create the library directory" in out
        assert str(dirs) in out, "the message must name the file to edit"
    finally:
        blocked.chmod(0o700)


def test_shipped_dirs_json_still_points_at_the_container_mount():
    """Docker must be byte-for-byte unaffected by the dirs.json indirection."""
    shipped = json.loads((REPO_ROOT / "dirs.json").read_text(encoding="utf-8"))
    assert shipped["calibre_library_dir"] == "/calibre-library"


def test_container_layout_resolves_to_exactly_the_old_literals(app_paths, monkeypatch):
    """Docker parity — the whole point is that only *non*-container installs move.

    Under the container's own environment every resolver must return precisely
    the string that used to be hardcoded. If this drifts, a de-hardcoding
    intended to help bare-metal users has silently relocated the databases of
    every Docker user instead.
    """
    monkeypatch.setenv("CWA_APP_ROOT", "/app/calibre-web-automated")
    monkeypatch.setenv("CALIBRE_DBPATH", "/config")  # Dockerfile ENV, per #1162

    assert str(app_paths.app_root()) == "/app/calibre-web-automated"
    assert str(app_paths.dirs_json()) == "/app/calibre-web-automated/dirs.json"
    assert str(app_paths.empty_library_file("app.db")) == (
        "/app/calibre-web-automated/empty_library/app.db"
    )
    assert str(app_paths.empty_library_file("metadata.db")) == (
        "/app/calibre-web-automated/empty_library/metadata.db"
    )
    assert str(app_paths.script_path("cover_enforcer.py")) == (
        "/app/calibre-web-automated/scripts/cover_enforcer.py"
    )
    assert str(app_paths.config_dir()) == "/config"
    assert str(app_paths.app_db_path()) == "/config/app.db"


def test_container_layout_resolves_without_the_env_override(app_paths, monkeypatch, tmp_path):
    """Same parity, but proving the *derivation* lands there on its own.

    The container does not set ``CWA_APP_ROOT``; the app root has to fall out of
    the code's own location. Lay the tree out the way the image does and check
    the derived answer, rather than trusting the override path above.
    """
    monkeypatch.delenv("CWA_APP_ROOT", raising=False)
    app_dir = tmp_path / "app" / "calibre-web-automated"
    (app_dir / "scripts").mkdir(parents=True)
    (app_dir / "empty_library").mkdir()
    module_copy = app_dir / "scripts" / "app_paths.py"
    module_copy.write_text(
        (SCRIPTS_DIR / "app_paths.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    spec = importlib.util.spec_from_file_location("app_paths_container", module_copy)
    relocated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relocated)

    assert relocated.app_root() == app_dir
    assert relocated.dirs_json() == app_dir / "dirs.json"
    assert relocated.empty_library_file("app.db") == app_dir / "empty_library" / "app.db"


def test_sys_path_bootstrap_points_at_the_resolved_root(app_paths, monkeypatch):
    """The ``_CPS_ROOT`` sys.path inserts were literals too (4 call sites).

    The repo root is often already on sys.path under pytest, so a no-op
    implementation would pass a bare membership check. Clear it first, then
    assert position, return value and idempotency.
    """
    before = list(sys.path)
    try:
        root = str(REPO_ROOT)
        sys.path[:] = [p for p in sys.path if p != root]
        assert root not in sys.path

        returned = app_paths.ensure_app_root_on_sys_path()
        assert returned == root
        assert sys.path[0] == root, "must go to the front, ahead of scripts/"

        app_paths.ensure_app_root_on_sys_path()
        assert sys.path.count(root) == 1, "second call must not duplicate the entry"
    finally:
        sys.path[:] = before
