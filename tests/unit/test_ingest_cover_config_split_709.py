"""Regression test for fork #709.

During ingest auto-metadata-fetch, the cover-apply path calls
``config.get_book_path()`` (cps/helper.py save_cover), which reads
``config_calibre_split`` / ``config_calibre_split_dir``. In the ingest
process, config is populated by ingest_processor._load_cps_settings_from_app_db
(a minimal raw-SQL loader), NOT the main app's config.load(). That loader used
to omit the split-library columns, so get_book_path() raised
``'ConfigSQL' object has no attribute 'config_calibre_split'`` and the cover was
never written (reporter @maraken).

These tests pin that the minimal loader populates the split-library settings so
get_book_path() works for both plain and split libraries.
"""
import os
import sqlite3
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _make_app_db(path, *, split=0, split_dir=None, calibre_dir="/calibre-library"):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE settings ("
        "config_use_google_drive INTEGER, config_google_drive_folder TEXT, "
        "config_calibre_dir TEXT, config_certfile TEXT, config_keyfile TEXT, "
        "config_calibre_split INTEGER, config_calibre_split_dir TEXT)"
    )
    con.execute(
        "INSERT INTO settings VALUES (?,?,?,?,?,?,?)",
        (0, None, calibre_dir, None, None, split, split_dir),
    )
    con.commit()
    con.close()


@pytest.fixture()
def ingest_processor(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    return importlib.import_module("ingest_processor")


def _fresh_config():
    from cps import config_sql
    return config_sql.ConfigSQL()


def test_plain_library_loads_split_flag_false(ingest_processor, monkeypatch, tmp_path):
    app_db = tmp_path / "app.db"
    _make_app_db(app_db, split=0, calibre_dir="/calibre-library")
    monkeypatch.setenv("CWA_APP_DB_PATH", str(app_db))

    cfg = _fresh_config()
    monkeypatch.setattr(ingest_processor, "_cps_config", cfg)
    ingest_processor._load_cps_settings_from_app_db()

    # The attribute must exist (absence is the #709 bug), and be False for a
    # plain library — get_book_path() must then return the calibre dir, not raise.
    assert cfg.config_calibre_split is False
    assert cfg.get_book_path() == "/calibre-library"


def test_split_library_routes_book_path_to_split_dir(ingest_processor, monkeypatch, tmp_path):
    app_db = tmp_path / "app.db"
    _make_app_db(app_db, split=1, split_dir="/books", calibre_dir="/calibre-library")
    monkeypatch.setenv("CWA_APP_DB_PATH", str(app_db))

    cfg = _fresh_config()
    monkeypatch.setattr(ingest_processor, "_cps_config", cfg)
    ingest_processor._load_cps_settings_from_app_db()

    assert cfg.config_calibre_split is True
    assert cfg.config_calibre_split_dir == "/books"
    assert cfg.get_book_path() == "/books"
