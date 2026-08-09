"""Regression coverage for nested Calibre libraries (fork issue #1436)."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from cwa_db import CWA_DB  # noqa: E402
import kindle_epub_fixer  # noqa: E402


def _nested_library(tmp_path):
    mount_root = tmp_path / "calibre-library"
    # URI-significant characters prove the read-only SQLite URI is encoded.
    library = mount_root / "ebooks #1?"
    library.mkdir(parents=True)
    metadata_db = library / "metadata.db"
    with sqlite3.connect(metadata_db) as connection:
        connection.execute("CREATE TABLE books (id INTEGER, timestamp TEXT)")
        connection.execute("INSERT INTO books VALUES (1, '2025-01-02 03:04:05')")
        connection.execute("CREATE TABLE book_format_checksums (book INTEGER)")
    dirs_json = tmp_path / "dirs.json"
    dirs_json.write_text(json.dumps({"calibre_library_dir": str(library)}))
    return mount_root, library, dirs_json


def test_stats_resolve_nested_library_without_creating_root_db(tmp_path, monkeypatch):
    mount_root, _library, dirs_json = _nested_library(tmp_path)
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_json))
    real_connect = sqlite3.connect

    # Model /calibre-library with a temporary mount root on both pre-fix and
    # fixed code. The old hardcoded connect is allowed to exhibit its bug here.
    def mounted_connect(database, *args, **kwargs):
        if database == "/calibre-library/metadata.db":
            database = mount_root / "metadata.db"
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", mounted_connect)

    stats = CWA_DB.__new__(CWA_DB)
    assert stats.get_library_growth(days=10000) == [("2025-01-02", 1)]
    assert not (mount_root / "metadata.db").exists()


def test_missing_nested_db_degrades_without_creating_root_db(tmp_path, monkeypatch):
    mount_root = tmp_path / "calibre-library"
    nested = mount_root / "ebooks"
    nested.mkdir(parents=True)
    dirs_json = tmp_path / "dirs.json"
    dirs_json.write_text(json.dumps({"calibre_library_dir": str(nested)}))
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_json))
    real_connect = sqlite3.connect

    def mounted_connect(database, *args, **kwargs):
        if database == "/calibre-library/metadata.db":
            database = mount_root / "metadata.db"
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", mounted_connect)

    stats = CWA_DB.__new__(CWA_DB)
    assert stats.get_library_growth() == []
    assert not (mount_root / "metadata.db").exists()
    assert not (nested / "metadata.db").exists()


def test_kindle_fixer_resolves_nested_library_from_dirs_json(tmp_path, monkeypatch):
    mount_root, library, dirs_json = _nested_library(tmp_path)
    monkeypatch.setattr(kindle_epub_fixer, "dirs_json", str(dirs_json))

    fixer = kindle_epub_fixer.EPUBFixer.__new__(kindle_epub_fixer.EPUBFixer)
    assert fixer._get_metadata_db_path() == str(library / "metadata.db")
    assert not (mount_root / "metadata.db").exists()


def test_checksum_service_polls_and_backfills_nested_library(tmp_path):
    mount_root, library, dirs_json = _nested_library(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocation = tmp_path / "backfill-args"
    sqlite_invocation = tmp_path / "sqlite-args"
    wrapper = bin_dir / "cwa-as-abc"
    wrapper.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$BACKFILL_ARGS"\n')
    wrapper.chmod(0o755)
    sqlite_wrapper = bin_dir / "sqlite3"
    sqlite_wrapper.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$SQLITE_ARGS"\n'
        'printf "%s\\n" book_format_checksums\n'
    )
    sqlite_wrapper.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "CWA_DIRS_JSON": str(dirs_json),
        "BACKFILL_ARGS": str(invocation),
        "SQLITE_ARGS": str(sqlite_invocation),
        "PATH": f"{bin_dir}:{env['PATH']}",
    })

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-checksum-backfill/run")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )

    assert "Database schema ready (attempt 1)" in result.stdout
    assert ["--library-path", str(library)] == invocation.read_text().splitlines()[2:4]
    assert sqlite_invocation.read_text().splitlines()[0] == (
        (library / "metadata.db").as_uri() + "?mode=ro"
    )
    assert not (mount_root / "metadata.db").exists()
