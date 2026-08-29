# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for #1883's config-root path resolution."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def script_config_root(monkeypatch, tmp_path):
    """Point the dependency-free scripts resolver at a bare-metal config."""
    config_root = tmp_path / "bare-metal-config"
    config_root.mkdir()
    monkeypatch.setenv("CALIBRE_DBPATH", str(config_root))
    app_paths = importlib.reload(importlib.import_module("app_paths"))
    return app_paths, config_root


def test_scripts_config_helpers_honour_calibre_dbpath(script_config_root):
    app_paths, config_root = script_config_root

    assert app_paths.config_path("convert-library.log") == (
        config_root / "convert-library.log"
    )
    assert app_paths.processed_books_dir() == config_root / "processed_books"


def test_ingest_processed_books_calls_use_resolved_root(
    script_config_root, monkeypatch
):
    _app_paths, config_root = script_config_root
    ingest_processor = importlib.import_module("ingest_processor")
    monkeypatch.setattr(ingest_processor, "backup_destinations", {})

    ingest_processor._ensure_processed_books_dirs()
    processed_root = config_root / "processed_books"
    assert {
        child.name for child in processed_root.iterdir() if child.is_dir()
    } == {"converted", "imported", "fixed_originals", "failed"}

    ingest_processor._load_backup_destinations()
    assert ingest_processor.backup_destinations["failed"] == str(
        processed_root / "failed"
    )
    assert ingest_processor.failed_backup_dir() == str(processed_root / "failed")


def test_convert_library_calls_use_resolved_root(
    script_config_root, monkeypatch
):
    _app_paths, config_root = script_config_root
    convert_library = importlib.reload(importlib.import_module("convert_library"))
    processed_root = config_root / "processed_books"
    failed_root = processed_root / "failed"

    monkeypatch.setattr(
        convert_library,
        "backup_destinations",
        convert_library._load_backup_destinations(),
    )

    assert convert_library.convert_library_log_file == str(
        config_root / "convert-library.log"
    )
    assert convert_library.backup_destinations["failed"] == str(failed_root)
    assert convert_library.failed_backup_dir() == str(failed_root)

    source = config_root / "source.epub"
    source.write_bytes(b"book")
    monkeypatch.setattr(convert_library, "backup_destinations", {})
    converter = object.__new__(convert_library.LibraryConverter)
    converter.backup(str(source), "converted")
    assert (processed_root / "converted" / source.name).read_bytes() == b"book"


def test_auto_zip_uses_resolved_processed_books_root(
    script_config_root, monkeypatch
):
    _app_paths, config_root = script_config_root
    auto_zip = importlib.import_module("auto_zip")

    class _FakeDB:
        cwa_settings = {"auto_zip_backups": True}

    monkeypatch.setattr(auto_zip, "CWA_DB", _FakeDB)
    monkeypatch.setattr(auto_zip.AutoZipper, "get_books_to_zip", lambda self: {})

    zipper = auto_zip.AutoZipper()
    expected = f"{config_root / 'processed_books'}{os.sep}"
    assert zipper.archive_dirs_stem == expected
    assert zipper.failed_dir == f"{expected}failed/"


def test_kindle_fixer_backup_uses_resolved_processed_books_root(
    script_config_root, monkeypatch
):
    _app_paths, config_root = script_config_root
    kindle_epub_fixer = importlib.import_module("kindle_epub_fixer")
    copied = []
    monkeypatch.setattr(
        kindle_epub_fixer.shutil,
        "copy2",
        lambda source, destination: copied.append((source, destination)),
    )

    fixer = object.__new__(kindle_epub_fixer.EPUBFixer)
    fixer.cwa_settings = {"auto_backup_epub_fixes": True}
    fixer.manually_triggered = False
    fixer.backup_original_file("/library/book.epub")

    assert copied == [(
        "/library/book.epub",
        str(config_root / "processed_books" / "fixed_originals"),
    )]


def test_kindle_fixer_creates_missing_backup_directory(script_config_root):
    _app_paths, config_root = script_config_root
    kindle_epub_fixer = importlib.import_module("kindle_epub_fixer")
    source = config_root / "book.epub"
    source.write_bytes(b"original book")
    (config_root / "processed_books").mkdir()
    backup_dir = config_root / "processed_books" / "fixed_originals"

    assert not backup_dir.exists()

    fixer = object.__new__(kindle_epub_fixer.EPUBFixer)
    fixer.cwa_settings = {"auto_backup_epub_fixes": True}
    fixer.manually_triggered = False
    fixer.backup_original_file(str(source))

    assert backup_dir.is_dir()
    assert (backup_dir / source.name).read_bytes() == b"original book"


def test_cps_call_sites_follow_resolved_config_root(monkeypatch, tmp_path):
    from cps import constants, cwa_functions, duplicates
    from cps.tasks import ops

    config_root = tmp_path / "cps-config"
    monkeypatch.setattr(constants, "CONFIG_DIR", str(config_root))

    assert constants.processed_books_dir() == str(config_root / "processed_books")
    assert duplicates.duplicate_resolution_root() == str(
        config_root / "processed_books" / "duplicate_resolutions"
    )
    assert cwa_functions._service_log_path("convert-library.log") == str(
        config_root / "convert-library.log"
    )
    assert ops.TaskConvertLibraryRun().log_path == str(
        config_root / "convert-library.log"
    )
    assert ops.TaskEpubFixerRun().log_path == str(
        config_root / "epub-fixer.log"
    )


def test_metadata_lock_default_follows_config_resolver(monkeypatch, tmp_path):
    from cps.services import calibre_db_lock

    config_root = tmp_path / "lock-config"
    monkeypatch.delenv("CWA_METADATA_LOCK_DIR", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(config_root / "app.db"))
    assert calibre_db_lock._resolve_lock_path(None) == str(
        config_root / calibre_db_lock.DEFAULT_LOCK_BASENAME
    )

    explicit = tmp_path / "explicit-locks"
    monkeypatch.setenv("CWA_METADATA_LOCK_DIR", str(explicit))
    assert calibre_db_lock._resolve_lock_path(None) == str(
        explicit / calibre_db_lock.DEFAULT_LOCK_BASENAME
    )


@pytest.mark.parametrize(
    "relative_path",
    (
        "scripts/ingest_processor.py",
        "scripts/convert_library.py",
        "scripts/auto_zip.py",
        "scripts/kindle_epub_fixer.py",
        "cps/duplicates.py",
        "cps/templates/duplicates.html",
        "cps/templates/cwa_settings.html",
    ),
)
def test_processed_books_sites_do_not_restore_container_literal(relative_path):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "/config/processed_books" not in source


@pytest.mark.parametrize(
    "relative_path",
    (
        "scripts/convert_library.py",
        "cps/cwa_functions.py",
        "cps/tasks/ops.py",
    ),
)
def test_convert_log_sites_do_not_restore_container_literal(relative_path):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "/config/convert-library.log" not in source


def test_metadata_lock_has_no_container_default_literal():
    source = (
        REPO_ROOT / "cps/services/calibre_db_lock.py"
    ).read_text(encoding="utf-8")
    assert "DEFAULT_LOCK_DIR" not in source
