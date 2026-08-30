# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh app.db creation includes the configuration schema (#2047)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from auto_library import create_appdb


def test_create_appdb_seeds_usable_settings_row(tmp_path):
    """The real first-boot path must be writable before auto-library uses it."""
    app_db = tmp_path / "app.db"
    library_dir = tmp_path / "calibre-library"

    create_appdb(str(app_db))

    with sqlite3.connect(app_db) as connection:
        result = connection.execute(
            "UPDATE settings SET config_calibre_dir = ?",
            (str(library_dir),),
        )
        assert result.rowcount == 1
        stored_path = connection.execute(
            "SELECT config_calibre_dir FROM settings"
        ).fetchone()

    assert stored_path == (str(library_dir),)


def test_update_calibre_web_db_exits_when_settings_row_is_missing(
    tmp_path, capsys
):
    """A structurally unusable app.db must stop boot, not look configured."""
    from auto_library import AutoLibrary

    app_db = tmp_path / "app.db"
    create_appdb(str(app_db))
    with sqlite3.connect(app_db) as connection:
        connection.execute("DELETE FROM settings")

    auto_library = AutoLibrary.__new__(AutoLibrary)
    auto_library.app_db = str(app_db)
    auto_library.lib_path = str(tmp_path / "calibre-library")

    with pytest.raises(SystemExit) as exit_info:
        auto_library.update_calibre_web_db()

    assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert "FATAL" in output
    assert "expected exactly one settings row; updated 0" in output
    assert "app.db would be left unconfigured" in output
