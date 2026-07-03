# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #102 (mirrors CWA #1340 by user MoritzH).

Pre-investigation hypothesis: CWA modifies the EPUB during download
(metadata embed) and the new checksum is not written to
book_format_checksums, so KOReader sync fails matching the post-download
file the user transferred to their device.

Phase 3.5 step 5 (reproduce) outcome: NOT REPRODUCIBLE. Our fork
already ships the fix:

  1. PR #94 (v4.0.28) wired `calculate_and_store_checksum()` into
     `do_download_file()` after every metadata embed (kepubify or
     calibre-export branches), gated on `is_koreader_sync_enabled()`.

  2. `BookFormatChecksum` is INSERT-only (not UPSERT/REPLACE), so each
     post-embed checksum is appended as a new history row. The
     original-import checksum is preserved alongside it.

  3. `get_book_by_checksum()` matches against ANY stored checksum
     for the book/format pair, so KOReader pushes from devices using
     either the original-import file OR a post-download-modified file
     find the same book.

The CWA #1340 reporter is on CWA v4.0.6 — predates our fix entirely.

Rather than ship a speculative additional fix, these tests pin the
existing behavior so a future refactor that quietly drops history
tracking (e.g. switches the INSERT to an UPSERT, or makes the call
conditional on something other than `is_koreader_sync_enabled`) gets
caught at CI.
"""

import os
import pytest
import sqlite3

from cps.progress_syncing.checksums import (
    store_checksum,
    calculate_and_store_checksum,
)
from cps.progress_syncing.models import ensure_checksum_table


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "metadata.db"
    conn = sqlite3.connect(str(db_path))
    ensure_checksum_table(conn)
    yield conn
    conn.close()


@pytest.fixture
def epub_v1(tmp_path):
    """Original-import file."""
    p = tmp_path / "book_v1.epub"
    p.write_bytes(b"Original book content with import-time metadata" * 50)
    return str(p)


@pytest.fixture
def epub_v2(tmp_path):
    """Post-embed file with different content (different checksum)."""
    p = tmp_path / "book_v2.epub"
    p.write_bytes(b"Same book, embedded metadata makes content differ" * 50)
    return str(p)


@pytest.mark.unit
class TestChecksumHistoryPreserved:
    """Pin the user-visible expectation from #102: every download's
    checksum is persisted, the original is not lost."""

    def test_storing_two_checksums_appends_both(self, test_db):
        """Pre-fix bug claim: only the original is stored. Post-fix:
        both rows in the table after two distinct checksums."""
        store_checksum(42, 'EPUB', 'original_hash', db_connection=test_db)
        store_checksum(42, 'EPUB', 'post_embed_hash', db_connection=test_db)
        rows = test_db.execute(
            "SELECT checksum FROM book_format_checksums WHERE book = 42"
        ).fetchall()
        assert len(rows) == 2, (
            "store_checksum must INSERT (preserve history); UPSERT would lose "
            "the original-import checksum and break KOReader sync from devices "
            "using the un-embedded file"
        )
        checksums = {r[0] for r in rows}
        assert checksums == {'original_hash', 'post_embed_hash'}

    def test_calculate_and_store_real_files_preserves_history(
            self, test_db, epub_v1, epub_v2):
        """End-to-end: two real files with different content → two
        different checksums → both stored. Mirrors the user-reported
        flow (import once, download once, two distinct partial-MD5
        hashes from the same Calibre book)."""
        h1 = calculate_and_store_checksum(7, 'EPUB', epub_v1, db_connection=test_db)
        h2 = calculate_and_store_checksum(7, 'EPUB', epub_v2, db_connection=test_db)
        assert h1 is not None and h2 is not None
        assert h1 != h2, "different file contents must produce different partial-MD5"

        # Scope to the binary partial-MD5 channel: since #636,
        # calculate_and_store_checksum also registers a filename-digest row
        # (version 'koreader_filename') per call. That channel has its own
        # coverage in test_627_525_filename_checksum.py; this test's subject
        # is binary-checksum history preservation.
        rows = test_db.execute(
            "SELECT checksum FROM book_format_checksums"
            " WHERE book = 7 AND version = 'koreader' ORDER BY created"
        ).fetchall()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {h1, h2}


@pytest.mark.unit
class TestStoreChecksumIsInsertNotUpsert:
    """Pin the SQL semantics — `store_checksum` must use INSERT, not
    INSERT OR REPLACE / UPSERT. Replacing on conflict would drop history."""

    def test_source_uses_plain_insert(self):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "progress_syncing" / "checksums" /
               "manager.py").read_text()
        # The store_checksum function body must contain `INSERT INTO` and
        # must NOT contain `INSERT OR REPLACE`, `ON CONFLICT`, or `REPLACE INTO`
        # in that function. Cheap-but-precise check: split the file on `def `
        # and isolate the function.
        import re
        match = re.search(
            r"def store_checksum\(.*?(?=\n(?:def |class )\w)",
            src, re.DOTALL,
        )
        assert match is not None, "store_checksum function not found"
        body = match.group(0)
        assert "INSERT INTO book_format_checksums" in body, (
            "store_checksum must perform a plain INSERT to preserve history"
        )
        forbidden = ["INSERT OR REPLACE", "REPLACE INTO", "ON CONFLICT"]
        for bad in forbidden:
            assert bad not in body.upper(), (
                f"store_checksum must NOT use `{bad}` — that would clobber "
                f"the original-import checksum, regressing fork #102 behavior"
            )


@pytest.mark.unit
class TestDoDownloadFileCallsCalculateAfterEmbed:
    """Pin the `do_download_file → calculate_and_store_checksum` integration
    so a future edit to the download flow can't silently drop the
    post-embed checksum recompute."""

    def test_do_download_file_source_calls_calculate_and_store_checksum(self):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "helper.py").read_text()
        import re
        # find do_download_file body
        match = re.search(
            r"def do_download_file\(.*?\n(.*?)(?=\n(?:def |@)\w)",
            src, re.DOTALL,
        )
        assert match is not None, "do_download_file not found"
        body = match.group(1)
        assert "calculate_and_store_checksum" in body, (
            "do_download_file must call calculate_and_store_checksum after "
            "metadata embed — without it, KOReader sync against the "
            "post-download file fails to find the book (fork #102 / CWA #1340)"
        )
        assert "metadata_was_embedded" in body, (
            "the call must be gated on metadata_was_embedded so we don't "
            "redundantly recompute when no embed happened"
        )

    def test_call_is_gated_on_is_koreader_sync_enabled(self):
        """PR #94 (v4.0.28) gated this on the sync-enabled flag. Future
        edits that remove the gate would re-introduce the original
        upstream issue (#1183 — `no such table` errors on instances that
        never enabled KOReader sync)."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "helper.py").read_text()
        import re
        match = re.search(
            r"def do_download_file\(.*?\n(.*?)(?=\n(?:def |@)\w)",
            src, re.DOTALL,
        )
        body = match.group(1)
        # Pin both the import and the gate
        assert "is_koreader_sync_enabled" in body
        # rough position check — gate must precede the call
        gate_pos = body.find("is_koreader_sync_enabled")
        call_pos = body.find("calculate_and_store_checksum")
        # call_pos may match the import line first — find the second occurrence
        # which is the actual call
        call_pos2 = body.find("calculate_and_store_checksum", call_pos + 1)
        assert gate_pos > 0
        # Either the gate appears before the call, or both are inside the
        # `if metadata_was_embedded` block; the simpler pin is gate-before-call
        if call_pos2 != -1:
            assert gate_pos < call_pos2, (
                "is_koreader_sync_enabled() check must precede the actual "
                "calculate_and_store_checksum call"
            )


@pytest.mark.unit
class TestGetBookByChecksumMatchesAnyVersion:
    """Pin the lookup side: KOReader pushing progress with EITHER the
    original-import checksum OR a post-embed checksum must find the book.
    Without this, history-tracking is pointless."""

    def test_lookup_matches_any_stored_checksum_row(self):
        """Source-pin: `get_book_by_checksum` lives in
        `cps/progress_syncing/protocols/kosync.py` and queries the
        `BookFormatChecksum` table by checksum equality (matches any
        row, not just `latest`). Without this, history-tracking is
        pointless — devices using the original-import file would never
        find the book after a download regenerates the checksum."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "progress_syncing" / "protocols" /
               "kosync.py").read_text()
        import re
        match = re.search(
            r"def get_book_by_checksum\(.*?(?=\n(?:def |class )\w)",
            src, re.DOTALL,
        )
        assert match is not None, "get_book_by_checksum not found in kosync.py"
        body = match.group(0)
        assert "BookFormatChecksum.checksum == document_checksum" in body, (
            "lookup must match against ANY stored checksum row for the book — "
            "matching only `latest` would break sync from devices using the "
            "original-import file (fork #102 history-tracking expectation)"
        )
