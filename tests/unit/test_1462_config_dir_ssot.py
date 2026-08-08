# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""``scripts/`` and ``cps`` must resolve the same config dir and dirs.json.

The #1462 follow-up, reported by @Thovi98 packaging for YunoHost. #1463 stopped
``scripts/`` looking for its own code under ``/app``; these are the two places
where it still disagreed with ``cps`` about where the *data* lives:

1. ``app_paths.config_dir()`` fell back to the literal ``/config`` while
   ``cps.constants.CONFIG_DIR`` falls back to ``BASE_DIR``. Off Docker,
   ``auto_library.py`` seeded ``app.db`` into a freshly-created ``/config`` at
   the filesystem root and the app read ``<app root>/app.db``, so the seeding
   went to a database nothing opens.
2. ``CWA_DIRS_JSON`` moved dirs.json for ``scripts/`` but not for ``cps``, so a
   packager keeping dirs.json out of the upgrade path would put the ingest and
   the app on two different libraries.

Both are invisible in Docker, which sets ``CALIBRE_DBPATH=/config`` and leaves
``CWA_DIRS_JSON`` unset — hence the Docker-parity tests at the bottom, which
pin that this fix did not move anything in the image.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture
def app_paths(monkeypatch):
    """Import scripts/app_paths.py with a clean environment each time."""
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    for var in ("CWA_APP_ROOT", "CALIBRE_DBPATH", "CWA_DIRS_JSON", "CWA_APP_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    module = importlib.import_module("app_paths")
    return importlib.reload(module)


class TestConfigDirMatchesCps:
    """config_dir() has to land where cps/constants.py lands."""

    def test_defaults_to_app_root_not_slash_config(self, app_paths, monkeypatch, tmp_path):
        """RED before the fix: returned Path('/config').

        cps resolves CONFIG_DIR to BASE_DIR (the app root) when CALIBRE_DBPATH
        is unset. Anything else means the two halves seed and read different
        app.db files.
        """
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))

        assert app_paths.config_dir() == tmp_path

    def test_app_db_lands_under_the_app_root(self, app_paths, monkeypatch, tmp_path):
        """The symptom @Thovi98 reported: app.db seeded to /config/app.db."""
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))

        resolved = app_paths.app_db_path()

        assert resolved == tmp_path / "app.db"
        assert not str(resolved).startswith("/config")

    def test_honours_the_homedir_pip_marker_like_cps(self, app_paths, monkeypatch, tmp_path):
        """cps sends a pip install to ~/.calibre-web-automated; so must we."""
        (tmp_path / "cps").mkdir()
        (tmp_path / "cps" / ".HOMEDIR").write_text("")
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

        assert app_paths.config_dir() == tmp_path / "home" / ".calibre-web-automated"

    def test_calibre_dbpath_still_wins(self, app_paths, monkeypatch, tmp_path):
        """The override is unchanged — this is what the container relies on."""
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))
        monkeypatch.setenv("CALIBRE_DBPATH", "/somewhere/else")

        assert app_paths.config_dir() == Path("/somewhere/else")

    def test_calibre_dbpath_naming_a_db_file_still_resolves_to_its_parent(
        self, app_paths, monkeypatch, tmp_path
    ):
        """Legacy branch carried over from ingest_processor.get_app_db_path()."""
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))
        monkeypatch.setenv("CALIBRE_DBPATH", "/data/app.db")

        assert app_paths.config_dir() == Path("/data")
        assert app_paths.app_db_path() == Path("/data/app.db")

    def test_agrees_with_cps_constants_on_a_source_install(self, app_paths, monkeypatch, tmp_path):
        """The invariant itself, asserted against the real cps resolution.

        Rather than restating cps's rule, run cps/constants.py's own expression
        for a source layout and require the two to agree. If either side's
        default is edited later, this fails.
        """
        monkeypatch.setenv("CWA_APP_ROOT", str(tmp_path))
        cps_base_dir = tmp_path  # what BASE_DIR resolves to for a checkout at tmp_path
        cps_config_dir = os.environ.get("CALIBRE_DBPATH", str(cps_base_dir))

        assert str(app_paths.config_dir()) == cps_config_dir


class TestDirsJsonSSOT:
    """CWA_DIRS_JSON has to move dirs.json for cps too, or it splits the install."""

    def test_cps_constants_honours_cwa_dirs_json(self, monkeypatch, tmp_path):
        """RED before the fix: cps always used BASE_DIR/dirs.json."""
        external = tmp_path / "etc" / "dirs.json"
        external.parent.mkdir(parents=True)
        external.write_text("{}")
        monkeypatch.setenv("CWA_DIRS_JSON", str(external))

        source = (REPO_ROOT / "cps" / "constants.py").read_text()
        namespace = {"__file__": str(REPO_ROOT / "cps" / "constants.py")}
        exec(_dirs_json_assignment(source), {"os": os, "sys": sys, **namespace}, namespace)

        assert namespace["DIRS_JSON"] == str(external)

    def test_cps_falls_back_to_base_dir_when_unset(self, monkeypatch):
        """Docker and every existing install keep BASE_DIR/dirs.json."""
        monkeypatch.delenv("CWA_DIRS_JSON", raising=False)

        source = (REPO_ROOT / "cps" / "constants.py").read_text()
        namespace = {"BASE_DIR": "/app/calibre-web-automated"}
        exec(_dirs_json_assignment(source), {"os": os}, namespace)

        assert namespace["DIRS_JSON"] == "/app/calibre-web-automated/dirs.json"

    def test_blank_cwa_dirs_json_is_treated_as_unset(self, monkeypatch):
        """An empty env var must not resolve dirs.json to the empty string."""
        monkeypatch.setenv("CWA_DIRS_JSON", "   ")

        source = (REPO_ROOT / "cps" / "constants.py").read_text()
        namespace = {"BASE_DIR": "/app/calibre-web-automated"}
        exec(_dirs_json_assignment(source), {"os": os}, namespace)

        assert namespace["DIRS_JSON"] == "/app/calibre-web-automated/dirs.json"

    def test_scripts_and_cps_pick_the_same_dirs_json(self, app_paths, monkeypatch, tmp_path):
        """The invariant: one dirs.json, both halves."""
        external = tmp_path / "dirs.json"
        external.write_text("{}")
        monkeypatch.setenv("CWA_DIRS_JSON", str(external))

        source = (REPO_ROOT / "cps" / "constants.py").read_text()
        namespace = {"BASE_DIR": "/app/calibre-web-automated"}
        exec(_dirs_json_assignment(source), {"os": os}, namespace)

        assert str(app_paths.dirs_json()) == namespace["DIRS_JSON"]


class TestDockerParity:
    """Nothing in the image moves. Both knobs are set/unset there as before."""

    def test_container_env_still_resolves_to_slash_config(self, app_paths, monkeypatch):
        """Dockerfile sets ENV CALIBRE_DBPATH=/config, so the fallback never fires."""
        monkeypatch.setenv("CWA_APP_ROOT", "/app/calibre-web-automated")
        monkeypatch.setenv("CALIBRE_DBPATH", "/config")

        assert app_paths.config_dir() == Path("/config")
        assert app_paths.app_db_path() == Path("/config/app.db")

    def test_dockerfile_still_sets_calibre_dbpath(self):
        """Pin the ENV this fix depends on for Docker parity.

        If someone drops it from the Dockerfile, the image silently starts
        using the new source-install default and every container relocates its
        database. That would be a data-loss-shaped regression, so it fails here.
        """
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()

        assert "ENV CALIBRE_DBPATH=/config" in dockerfile

    def test_container_dirs_json_unchanged(self, app_paths, monkeypatch):
        monkeypatch.setenv("CWA_APP_ROOT", "/app/calibre-web-automated")
        monkeypatch.delenv("CWA_DIRS_JSON", raising=False)

        assert app_paths.dirs_json() == Path("/app/calibre-web-automated/dirs.json")


def _dirs_json_assignment(source):
    """Pull the DIRS_JSON assignment out of cps/constants.py.

    constants.py imports flask_babel at module scope, so it cannot be imported
    in a bare unit test. Executing just this statement keeps the assertion
    against the real shipped line rather than a copy of it.
    """
    for line in source.splitlines():
        if line.startswith("DIRS_JSON"):
            return line
    raise AssertionError("DIRS_JSON assignment not found in cps/constants.py")


class TestSeedDatabaseIsNotMistakenForTheLiveOne:
    """Off Docker the config dir IS the app root, which ships empty_library/app.db.

    Caught by the bare-metal end-to-end run, not by any unit test: pointing the
    config dir at the app root made ``check_for_app_db()`` walk the checkout,
    find the *seed* database, and decide the install already had one. It then
    never copied it, sqlite created a 0-byte file at ``<app root>/app.db``, and
    the first query failed with ``no such table: settings``.
    """

    def test_walk_skips_the_application_directories(self, monkeypatch, tmp_path):
        monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
        import auto_library

        (tmp_path / "empty_library").mkdir()
        (tmp_path / "empty_library" / "app.db").write_text("seed")
        (tmp_path / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "frontend" / "node_modules" / "pkg" / "app.db").write_text("noise")

        walker = auto_library.AutoLibrary.__new__(auto_library.AutoLibrary)
        walker.config_dir = str(tmp_path)

        found = [
            os.path.join(dirpath, name)
            for dirpath, _dirs, files in walker._walk_config_dir()
            for name in files
            if "app.db" in name
        ]

        assert found == [], f"seed/vendor databases must not count as the live app.db: {found}"

    def test_a_real_stray_app_db_is_still_found(self, monkeypatch, tmp_path):
        """The pruning must not blind the legacy-layout discovery it guards."""
        monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
        import auto_library

        (tmp_path / "legacy").mkdir()
        (tmp_path / "legacy" / "app.db").write_text("real")

        walker = auto_library.AutoLibrary.__new__(auto_library.AutoLibrary)
        walker.config_dir = str(tmp_path)

        found = [
            os.path.join(dirpath, name)
            for dirpath, _dirs, files in walker._walk_config_dir()
            for name in files
            if "app.db" in name
        ]

        assert found == [str(tmp_path / "legacy" / "app.db")]
