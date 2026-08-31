# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared SQLite environment and copy helpers."""

import os
import shutil
import sqlite3
from pathlib import Path


_TRUTHY_ENV_VALUES = frozenset(("1", "true", "yes", "on"))


def environment_flag_enabled(name, environ=None):
    """Return whether an environment flag uses a supported truthy spelling."""
    source = os.environ if environ is None else environ
    return source.get(name, "").strip().lower() in _TRUTHY_ENV_VALUES


def network_share_mode_enabled(environ=None):
    """Parse the process-wide ``NETWORK_SHARE_MODE`` contract in one place."""
    return environment_flag_enabled("NETWORK_SHARE_MODE", environ)


def copy_sqlite_database(source_path, destination_path, timeout=30, *, restore=False):
    """Copy a SQLite database transactionally, including committed WAL frames.

    SQLite's online backup API reads a consistent source snapshot even while
    other connections are writing it. It also writes an existing destination
    through SQLite's own transaction machinery, so a restore cannot combine a
    replacement main file with stale pages from the destination's ``-wal``.
    A newly-created destination is a self-contained database file; no source
    sidecar files need to be copied. Pass ``restore=True`` when the destination
    is the application's live database; its existing journal mode is retained.
    """
    source = os.path.abspath(os.fsdecode(os.fspath(source_path)))
    destination = os.path.abspath(os.fsdecode(os.fspath(destination_path)))
    if (
        os.path.normcase(os.path.realpath(source))
        == os.path.normcase(os.path.realpath(destination))
    ):
        raise ValueError("SQLite source and destination must be different files")

    destination_existed = os.path.exists(destination)
    source_uri = Path(source).as_uri() + "?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=timeout) as source_connection:
        with sqlite3.connect(destination, timeout=timeout) as destination_connection:
            destination_mode = destination_connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            source_connection.backup(destination_connection)
            # The backup copies page 1, including the source's persistent WAL
            # marker. A new backup file has no WAL sidecar by design, so make
            # it a portable rollback-journal database after all committed WAL
            # frames have landed in the main file. When replacing an existing
            # live database, retain its prior mode instead.
            requested_mode = (
                destination_mode if restore and destination_existed else "delete"
            )
            resulting_mode = destination_connection.execute(
                "PRAGMA journal_mode={}".format(requested_mode)
            ).fetchone()[0]
            if resulting_mode.lower() != requested_mode.lower():
                raise sqlite3.OperationalError(
                    "could not preserve destination journal mode {!r}".format(requested_mode)
                )

    if not restore:
        # SQLite may leave an empty shared-memory file behind after converting
        # the new snapshot from WAL to DELETE. A backup is deliberately one
        # portable file, and no other connection should have it open yet.
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(destination + suffix)
            except FileNotFoundError:
                pass

    # Match copy2's useful metadata preservation without making a successful
    # database snapshot fail on filesystems that reject timestamp/mode changes.
    try:
        shutil.copystat(source, destination)
    except OSError:
        pass
