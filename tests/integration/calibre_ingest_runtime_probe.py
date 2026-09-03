#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the destructive-overwrite boundary inside the built production image.

This is a companion executable for the host-side pytest.  It deliberately uses
the image's ordinary Python for ``ingest_processor`` and that processor's real
``calibre-debug`` helper subprocesses, so private Calibre Cache API drift is
exercised by the same runtime and call path as production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class _ImportLog:
    def import_add_entry(self, *_args) -> None:
        pass


def _processor(module, root: Path, source: Path):
    processor = object.__new__(module.NewBookProcessor)
    processor.target_format = "epub"
    processor.input_format = "epub"
    processor.is_kindle_epub_fixer = False
    processor.is_comic_flatten_comicinfo = False
    processor.cwa_settings = {
        "auto_ingest_automerge": "overwrite",
        "auto_backup_imports": False,
    }
    processor.library_dir = str(root / "library")
    processor.metadata_db = str(root / "library" / "metadata.db")
    processor.staging_dir = str(root / "staging")
    processor.tmp_conversion_dir = str(root / "conversion")
    processor.calibre_env = os.environ.copy()
    processor.filepath = str(source)
    processor.filename = source.name
    processor.original_filename = source.name
    processor.last_added_book_id = None
    processor.last_added_book_ids = []
    processor.last_added_ids_are_fallback = False
    processor.db = _ImportLog()

    for directory in (
        Path(processor.library_dir),
        Path(processor.staging_dir),
        Path(processor.tmp_conversion_dir),
        root / "processed_books" / "failed",
        root / "processed_books" / "overwritten",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    module.backup_destinations = {
        "failed": str(root / "processed_books" / "failed"),
        "overwritten": str(root / "processed_books" / "overwritten"),
    }
    module.wait_for_duplicate_full_scan_to_finish = lambda: None
    module.mark_ingest_batch_active = lambda: None
    module.clear_ingest_batch_active = lambda: None
    module.mark_ingest_batch_dirty = lambda: None
    module.gdrive_sync_if_enabled = lambda: None
    module.run_duplicate_scan_for_books = lambda *_args: None
    module._is_koreader_sync_enabled = lambda: False
    processor.record_original_filename = lambda: None
    processor.fetch_metadata_if_enabled = lambda *args, **kwargs: None
    processor._fix_unicode_path = lambda *_args: None
    processor.trigger_auto_send_if_enabled = lambda *args, **kwargs: None
    processor._comic_calibredb_metadata_args = lambda *_args: []
    processor._register_title_sort_function = lambda _connection: False
    return processor


def _marker_count(database: Path, source: Path) -> int:
    digest = _digest(source)
    marker = f"cwng_ingest_sha256_{digest}"
    with sqlite3.connect(database) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM identifiers WHERE type=? AND val=?",
                (marker, digest),
            ).fetchone()[0]
        )


def _book_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM books").fetchone()[0])


def _instrument(processor, *, inject_rollback: bool = False) -> dict:
    evidence = {
        "actions": [],
        "candidate_paths": [],
        "sanity": [],
        "recovery": [],
        "restore": [],
    }
    run_transaction = processor._run_calibre_transaction

    def tracked_transaction(*args):
        action = args[-1]
        result = run_transaction(*args)
        evidence["actions"].append(action)
        for item in result.get("formats", []):
            path = item["path"]
            if path not in evidence["candidate_paths"]:
                evidence["candidate_paths"].append(path)
        return result

    processor._run_calibre_transaction = tracked_transaction

    sanity_check = processor._incoming_file_is_sane

    def tracked_sanity(*args, **kwargs):
        result = sanity_check(*args, **kwargs)
        evidence["sanity"].append(bool(result))
        return result

    processor._incoming_file_is_sane = tracked_sanity

    preserve = processor._preserve_overwritten_formats

    def tracked_preserve(paths, staged):
        result = preserve(paths, staged)
        evidence["recovery"].append(bool(result))
        return result

    processor._preserve_overwritten_formats = tracked_preserve

    restore = processor._restore_overwritten_formats

    def tracked_restore(pairs):
        evidence["restore"].append(
            {
                "replacement_digest_before_restore": _digest(pairs[0][0]) if pairs else None,
                "preserved_digest": _digest(pairs[0][1]) if pairs else None,
            }
        )
        return restore(pairs)

    processor._restore_overwritten_formats = tracked_restore

    if inject_rollback:
        build_command = processor._calibre_transaction_command

        def failing_command(*args):
            command = build_command(*args)
            if args[-1] == "import":
                command.append("--fail-before-commit")
            return command

        processor._calibre_transaction_command = failing_command

    return evidence


def _scenario(module, fixture: Path, root: Path, *, inject_rollback: bool) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    existing = root / "existing.epub"
    incoming = root / "incoming.epub"
    shutil.copy2(fixture, existing)
    shutil.copy2(fixture, incoming)
    with incoming.open("ab") as stream:
        stream.write(
            b"\nCWNG overwrite rollback probe\n"
            if inject_rollback
            else b"\nCWNG successful overwrite probe\n"
        )

    seed = _processor(module, root, existing)
    existing_digest = _digest(existing)
    seed._run_calibre_transaction(
        existing,
        existing,
        existing_digest,
        existing_digest,
        {},
        "import",
    )
    assert _book_count(Path(seed.metadata_db)) == 1

    processor = _processor(module, root, incoming)
    evidence = _instrument(processor, inject_rollback=inject_rollback)
    expected_error = False
    try:
        processor.add_book_to_library(str(incoming))
    except module.RetryIngestSourceError:
        if not inject_rollback:
            raise
        expected_error = True

    assert evidence["candidate_paths"], "real same-format inspection returned no candidate"
    format_path = Path(evidence["candidate_paths"][-1])
    recovery_files = sorted(
        path
        for path in (root / "processed_books" / "overwritten").rglob("*")
        if path.is_file()
    )
    result = {
        **evidence,
        "expected_error": expected_error,
        "book_count": _book_count(Path(processor.metadata_db)),
        "marker_count": _marker_count(Path(processor.metadata_db), incoming),
        "existing_digest": existing_digest,
        "incoming_digest": _digest(incoming),
        "library_digest": _digest(format_path),
        "recovery_digests": [_digest(path) for path in recovery_files],
    }
    if inject_rollback:
        assert expected_error
        assert result["marker_count"] == 0
        assert result["library_digest"] == result["existing_digest"]
        assert result["restore"]
        assert result["restore"][0]["replacement_digest_before_restore"] == result["incoming_digest"]
    else:
        assert not expected_error
        assert result["marker_count"] == 1
        assert result["library_digest"] == result["incoming_digest"]
        assert result["sanity"] == [True]
        assert result["recovery"] == [True]
        assert result["actions"][-1] == "import"
    assert result["book_count"] == 1
    assert result["existing_digest"] in result["recovery_digests"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        default=Path("/app/calibre-web-automated/scripts"),
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.scripts_dir))
    import ingest_processor

    calibre_version_output = subprocess.run(
        ["calibre-debug", "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    calibre_version = calibre_version_output.splitlines()[-1]
    with tempfile.TemporaryDirectory(prefix="cwng-real-overwrite-") as temp_dir:
        base = Path(temp_dir)
        success = _scenario(ingest_processor, args.fixture, base / "success", inject_rollback=False)
        rollback = _scenario(ingest_processor, args.fixture, base / "rollback", inject_rollback=True)
    print(
        "CWNG_RUNTIME_PROBE="
        + json.dumps(
            {
                "calibre_version": calibre_version,
                "success": success,
                "rollback": rollback,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
