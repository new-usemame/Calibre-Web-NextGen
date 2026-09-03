# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Durability and transaction pins for F-1fdb7c."""

import inspect
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run"
HELPER = REPO_ROOT / "scripts/calibre_ingest_transaction.py"


def _shell_function(name: str) -> str:
    text = RUN_SCRIPT.read_text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}", start) + 2]


def test_both_queue_writers_use_the_durable_transaction():
    for name in ("queue_retry_file", "process_retry_queue"):
        body = _shell_function(name)
        assert 'mktemp "${QUEUE_FILE}.tmp.XXXXXX"' in body
        assert 'commit_queue_transaction "$temp_queue"' in body
        assert "report_queue_commit_failure" in body


def test_queue_commit_preserves_attributes_and_fsyncs_file_and_directory():
    body = _shell_function("commit_queue_transaction")
    assert "stat.S_IMODE(target_stat.st_mode)" in body
    assert "os.chown(staged, target_stat.st_uid, target_stat.st_gid" in body
    assert "os.fsync(stream.fileno())" in body
    assert "os.replace(staged, target)" in body
    assert "os.fsync(directory_fd)" in body


def test_startup_sweeps_abandoned_queue_transactions():
    source = RUN_SCRIPT.read_text()
    initialization = source[:source.index("# Create status file")]
    assert 'queue + ".tmp.*"' in initialization
    assert 'queue + ".trim.*"' in initialization
    assert "cleanup_queue_transaction_temps" in initialization


def test_retry_queue_has_no_post_commit_ledger_or_path_metadata_identity():
    source = RUN_SCRIPT.read_text()
    processor = (REPO_ROOT / "scripts/ingest_processor.py").read_text()
    assert "COMPLETION_LEDGER" not in source
    assert "CWA_INGEST_IDENTITY" not in source
    assert "record_completed_ingest_identity" not in processor
    assert "cannot represent a path containing a newline" in source


def test_staged_content_hash_is_checked_before_destructive_work(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    import ingest_processor

    source = inspect.getsource(ingest_processor.NewBookProcessor.add_book_to_library)
    hash_position = source.index("source_digest = _sha256_file(staged_identity_path)")
    lookup_position = source.index("_content_marker_book_ids(source_digest)")
    inspection_position = source.index("_current_overwrite_candidates(", lookup_position)
    validation_position = source.index("_prepare_destructive_overwrite(")
    transaction_position = source.index(
        "transaction_result = self._run_calibre_transaction(", validation_position
    )
    assert hash_position < lookup_position < inspection_position < validation_position < transaction_position


def test_calibre_helper_rechecks_and_marks_inside_one_transaction():
    source = HELPER.read_text()
    transaction = source.index("with cache.write_lock, cache.backend.conn:")
    recheck = source.index("existing = marker_book_ids(cache, source_digest)", transaction)
    add = source.index("add_with_automerge(", recheck)
    marker = source.index("attach_marker(cache, marker_targets, digest)")
    assert transaction < recheck < add
    assert marker < source.index("cache.dump_metadata(book_ids=marker_targets)")
    assert "MARKER_PREFIX = \"cwng_ingest_sha256_\"" in source


def test_transaction_prose_limits_atomicity_to_database_state():
    helper = HELPER.read_text()
    changelog = (REPO_ROOT / "changelog.d/ingest-integrity-retry-stable-overwrite.md").read_text()
    retry_finding = (REPO_ROOT / "findings/items/F-1fdb7c.json").read_text()
    overwrite_finding = (REPO_ROOT / "findings/items/F-1b2fdd.json").read_text()

    assert "format files are not rolled back" in helper
    assert "row/format mutation" not in retry_finding
    assert "transactional overwrite" not in overwrite_finding
    assert "database row and identifier" in changelog
    assert "format-file writes are not transactional" in changelog
