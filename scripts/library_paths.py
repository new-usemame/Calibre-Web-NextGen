"""Resolve the active Calibre library without creating database files."""

import json
import os
import sqlite3
from pathlib import Path

import app_paths

# Both defaults live in app_paths (#1462) so a relocated checkout resolves them
# the same way everywhere.
DEFAULT_LIBRARY_DIR = app_paths.DEFAULT_LIBRARY_DIR


def get_calibre_library_dir(dirs_json_path=None):
    """Return the configured library directory, with the legacy root as fallback."""
    config_path = dirs_json_path or str(app_paths.dirs_json())
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            configured = json.load(config_file).get("calibre_library_dir")
        if isinstance(configured, str) and configured.strip():
            return configured
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_LIBRARY_DIR


def get_calibre_metadata_db_path(dirs_json_path=None):
    """Return the active metadata.db path without touching the filesystem."""
    return os.path.join(get_calibre_library_dir(dirs_json_path), "metadata.db")


def connect_calibre_metadata_db(dirs_json_path=None, timeout=10):
    """Open the active metadata.db read-only, refusing to create a missing file."""
    db_path = get_calibre_metadata_db_path(dirs_json_path)
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    return sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True, timeout=timeout)
