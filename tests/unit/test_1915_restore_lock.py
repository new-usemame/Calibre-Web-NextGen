# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for the last-resort restore's process locks (#1915)."""

import fcntl
import inspect
import os

import pytest

from cps import admin


pytestmark = pytest.mark.unit


def _restore_lock_api():
    acquire = getattr(admin, "_acquire_restore_lock", None)
    release = getattr(admin, "_release_restore_locks", None)
    assert callable(acquire), "restore flock acquisition seam is missing"
    assert callable(release), "restore flock release seam is missing"
    return acquire, release


def _service_lock_api():
    acquire = getattr(admin, "_acquire_restore_service_locks", None)
    release = getattr(admin, "_release_restore_locks", None)
    assert callable(acquire), "service-lock pause decision seam is missing"
    assert callable(release), "restore flock release seam is missing"
    return acquire, release


def test_dead_pid_sentinel_does_not_block_restore(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.tempfile, "gettempdir", lambda: str(tmp_path))
    lock_path = tmp_path / "restore_calibre_db.lock"
    lock_path.write_text("999999", encoding="utf-8")
    acquire, release = _restore_lock_api()

    handle = acquire()

    try:
        assert handle is not None
        assert lock_path.read_text(encoding="utf-8") == "999999"
    finally:
        release([handle] if handle is not None else [])


def test_held_ingest_lock_refuses_before_backup_or_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.tempfile, "gettempdir", lambda: str(tmp_path))
    acquire, release = _service_lock_api()
    ingest_path = tmp_path / "ingest_processor.lock"
    ingest_holder = open(ingest_path, "a+", encoding="utf-8")
    fcntl.flock(ingest_holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    backup_dir = tmp_path / "backup"
    metadata_db = tmp_path / "metadata.db"
    app_db = tmp_path / "app.db"
    metadata_db.write_bytes(b"metadata-before")
    app_db.write_bytes(b"app-before")
    copy_calls = []
    monkeypatch.setattr(
        admin.shutil,
        "copy2",
        lambda source, destination: copy_calls.append((source, destination)),
    )

    handles = []
    try:
        handles, blocking_service = acquire()
        if blocking_service is None:
            backup_dir.mkdir()
            admin.shutil.copy2(metadata_db, backup_dir / "metadata.db.bak")
            admin.shutil.copy2(app_db, backup_dir / "app.db.bak")

        assert blocking_service == "ingest"
        assert not backup_dir.exists()
        assert copy_calls == []
        assert metadata_db.read_bytes() == b"metadata-before"
        assert app_db.read_bytes() == b"app-before"

        handler_source = inspect.getsource(admin.restore_calibre_db)
        decision_index = handler_source.find("_acquire_restore_service_locks()")
        backup_index = handler_source.index("os.makedirs(backup_dir")
        assert 0 <= decision_index < backup_index, (
            "the service-lock refusal decision must precede backup creation"
        )
    finally:
        release(handles)
        fcntl.flock(ingest_holder.fileno(), fcntl.LOCK_UN)
        ingest_holder.close()


@pytest.mark.parametrize(
    ("lock_name", "blocking_service"),
    [
        ("ingest_processor.lock", "ingest"),
        ("cover_enforcer.lock", "cover_enforcer"),
    ],
)
def test_held_service_lock_is_a_hard_refusal(
    monkeypatch, tmp_path, lock_name, blocking_service
):
    monkeypatch.setattr(admin.tempfile, "gettempdir", lambda: str(tmp_path))
    acquire, release = _service_lock_api()
    holder = open(tmp_path / lock_name, "a+", encoding="utf-8")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    handles = []
    try:
        handles, blocked_by = acquire()

        assert handles == []
        assert blocked_by == blocking_service
    finally:
        release(handles)
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_two_concurrent_restore_acquirers_allow_exactly_one(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.tempfile, "gettempdir", lambda: str(tmp_path))
    acquire, release = _restore_lock_api()

    handles = [acquire(), acquire()]

    try:
        assert sum(handle is not None for handle in handles) == 1
    finally:
        release([handle for handle in handles if handle is not None])


def test_restore_lock_paths_are_never_unlinked_or_truncated():
    source = inspect.getsource(admin.restore_calibre_db)
    acquire = getattr(admin, "_acquire_restore_file_lock", None)
    assert callable(acquire), "shared non-truncating flock seam is missing"
    acquire_source = inspect.getsource(acquire)

    assert "os.remove(lock_path)" not in source
    assert 'open(lock_path, "a+"' in acquire_source
    assert 'open(lock_path, "w"' not in acquire_source


def test_service_lock_refusals_have_translatable_admin_messages():
    source = inspect.getsource(admin.restore_calibre_db)

    assert '_("An ingest run is in progress; try again when it finishes.")' in source
    assert '_("Cover enforcement is in progress; try again when it finishes.")' in source
