# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #525 / #627 — KOReader sync filename-hash channel.

KOReader's kosync plugin (and Crossink/x4) can identify a document by a hash
of its FILENAME instead of the partial-MD5 of its bytes. CW-NG only ever
stored the binary partial-MD5, so:

- #525: clients in 'filename' matching mode always get "No book found".
- #627/#633 class: device files whose bytes no longer match any stored
  binary checksum (pre-update downloads, metadata re-embeds) cannot sync at
  all; the filename channel is the no-re-download rescue path.

The filename digest algorithm was byte-verified against the #525 reporter's
oracle: md5("More Everything Forever - Adam Becker.epub") ==
9ea1b31e133214bb1169acce6ff4affb (UTF-8, basename WITH lowercase extension,
no case folding). See notes/525-kosync-filename-matching-design.md.
"""

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

from cps.progress_syncing.checksums import (
    CHECKSUM_VERSION,
    FILENAME_CHECKSUM_VERSION,
    calculate_and_store_checksum,
    calculate_koreader_filename_md5,
)

# The #525 reporter's real-world oracle (Crossink sent this hash for this file).
ORACLE_BASENAME = "More Everything Forever - Adam Becker.epub"
ORACLE_DIGEST = "9ea1b31e133214bb1169acce6ff4affb"

CHECKSUM_TABLE_SQL = '''
    CREATE TABLE book_format_checksums (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book INTEGER NOT NULL,
        format TEXT NOT NULL COLLATE NOCASE,
        checksum TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT 'koreader',
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
'''


@pytest.mark.unit
class TestFilenameDigest:
    def test_matches_reporter_oracle(self):
        assert calculate_koreader_filename_md5(ORACLE_BASENAME) == ORACLE_DIGEST

    def test_plain_md5_of_utf8_bytes(self):
        name = "Ökobuch – Ää.epub"
        assert calculate_koreader_filename_md5(name) == \
            hashlib.md5(name.encode("utf-8")).hexdigest()

    def test_no_case_folding_or_extension_stripping(self):
        # Tried-and-rejected variants from the oracle brute-force must NOT
        # be what we compute.
        assert calculate_koreader_filename_md5(ORACLE_BASENAME.lower()) != ORACLE_DIGEST
        assert calculate_koreader_filename_md5(
            ORACLE_BASENAME.rsplit(".", 1)[0]) != ORACLE_DIGEST

    def test_empty_and_none_return_none(self):
        assert calculate_koreader_filename_md5("") is None
        assert calculate_koreader_filename_md5(None) is None

    def test_version_constant_distinct(self):
        assert FILENAME_CHECKSUM_VERSION == "koreader_filename"
        assert FILENAME_CHECKSUM_VERSION != CHECKSUM_VERSION


@pytest.mark.unit
class TestDownloadChokepointStoresBothVersions:
    """calculate_and_store_checksum (the download/export chokepoint,
    cps/helper.py caller) must register BOTH the binary partial-MD5 and the
    filename hash of the served basename."""

    @pytest.fixture
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(CHECKSUM_TABLE_SQL)
        conn.commit()
        yield conn
        conn.close()

    def test_stores_binary_and_filename_rows(self, conn, tmp_path):
        exported = tmp_path / ORACLE_BASENAME
        exported.write_bytes(b"epub bytes " * 500)

        result = calculate_and_store_checksum(
            book_id=7, book_format="EPUB", file_path=str(exported),
            db_connection=conn)
        assert result  # still returns the binary checksum

        rows = conn.execute(
            "SELECT version, checksum FROM book_format_checksums WHERE book=7"
        ).fetchall()
        by_version = dict(rows)
        assert set(by_version) == {CHECKSUM_VERSION, FILENAME_CHECKSUM_VERSION}
        assert by_version[CHECKSUM_VERSION] == result
        assert by_version[FILENAME_CHECKSUM_VERSION] == ORACLE_DIGEST

    def test_idempotent_on_repeat_download(self, conn, tmp_path):
        exported = tmp_path / ORACLE_BASENAME
        exported.write_bytes(b"epub bytes " * 500)
        for _ in range(2):
            calculate_and_store_checksum(
                book_id=7, book_format="EPUB", file_path=str(exported),
                db_connection=conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM book_format_checksums").fetchone()[0]
        assert count == 2  # one binary row + one filename row, no dupes


@pytest.mark.unit
class TestBackfillScriptFilenamePass:
    """The boot backfill (scripts/generate_book_checksums.py, run by the
    cwa-checksum-backfill s6 service) must register filename hashes for
    every (book, format) pair — including pairs that already carry binary
    rows, which the binary pass's any-row LEFT JOIN skips."""

    @pytest.fixture
    def library(self, tmp_path, monkeypatch):
        lib = tmp_path / "library"
        lib.mkdir()
        db = sqlite3.connect(lib / "metadata.db")
        db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT)")
        db.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT)")
        db.execute(CHECKSUM_TABLE_SQL)
        # Book 1: the oracle book, on disk, no checksum rows at all.
        db.execute("INSERT INTO books VALUES (1, 'More Everything Forever', 'oracle')")
        db.execute("INSERT INTO data VALUES (1, 1, 'EPUB', 'More Everything Forever - Adam Becker')")
        (lib / "oracle").mkdir()
        (lib / "oracle" / ORACLE_BASENAME).write_bytes(b"x" * 4096)
        # Book 2: already has a binary row (downloaded once) — the binary
        # pass skips it, but the filename pass must still cover it.
        db.execute("INSERT INTO books VALUES (2, 'Second Book', 'second')")
        db.execute("INSERT INTO data VALUES (2, 2, 'EPUB', 'Second Book - Author')")
        db.execute(
            "INSERT INTO book_format_checksums (book, format, checksum, version, created)"
            " VALUES (2, 'EPUB', 'deadbeef', 'koreader', '2026-01-01T00:00:00')")
        (lib / "second").mkdir()
        (lib / "second" / "Second Book - Author.epub").write_bytes(b"y" * 4096)
        db.commit()
        db.close()

        # Enabled cwa.db so the script's early-exit gate passes.
        cfg = tmp_path / "config"
        cfg.mkdir()
        cdb = sqlite3.connect(cfg / "cwa.db")
        cdb.execute("CREATE TABLE cwa_settings (koreader_sync_enabled INTEGER)")
        cdb.execute("INSERT INTO cwa_settings VALUES (1)")
        cdb.commit()
        cdb.close()
        monkeypatch.setenv("CWA_DB_PATH", str(cfg) + "/")
        return lib

    def _rows(self, library, version):
        conn = sqlite3.connect(library / "metadata.db")
        try:
            return conn.execute(
                "SELECT book, checksum FROM book_format_checksums"
                " WHERE version=? ORDER BY book", (version,)).fetchall()
        finally:
            conn.close()

    def test_filename_pass_covers_all_pairs(self, library):
        import generate_book_checksums as gbc
        gbc.generate_checksums(str(library), batch_size=10)

        fname_rows = self._rows(library, FILENAME_CHECKSUM_VERSION)
        assert fname_rows == [
            (1, ORACLE_DIGEST),
            (2, hashlib.md5(b"Second Book - Author.epub").hexdigest()),
        ]
        # Binary pass semantics unchanged: book 1 gained a binary row,
        # book 2's pre-existing row is untouched and not duplicated.
        bin_rows = self._rows(library, CHECKSUM_VERSION)
        assert [r[0] for r in bin_rows] == [1, 2]
        assert ("deadbeef" in dict(bin_rows).values()) or dict(bin_rows)[2] == "deadbeef"

    def test_filename_pass_idempotent(self, library):
        import generate_book_checksums as gbc
        gbc.generate_checksums(str(library), batch_size=10)
        first = self._rows(library, FILENAME_CHECKSUM_VERSION)
        gbc.generate_checksums(str(library), batch_size=10)
        assert self._rows(library, FILENAME_CHECKSUM_VERSION) == first

    def test_filename_pass_runs_when_binary_pass_has_nothing_to_do(self, library):
        """Caught live on cwn-local: when every pair already has a binary
        row, the binary pass early-returns ('All books already have
        checksums!') — the filename pass must still run."""
        import generate_book_checksums as gbc
        conn = sqlite3.connect(library / "metadata.db")
        conn.execute(
            "INSERT INTO book_format_checksums (book, format, checksum, version, created)"
            " VALUES (1, 'EPUB', 'cafebabe', 'koreader', '2026-01-01T00:00:00')")
        conn.commit()
        conn.close()

        gbc.generate_checksums(str(library), batch_size=10)
        assert [r[0] for r in self._rows(library, FILENAME_CHECKSUM_VERSION)] == [1, 2]


@pytest.mark.unit
class TestLookupIsVersionAgnostic:
    """Source-pin: get_book_by_checksum must keep matching ANY stored
    version when called without a version filter — that is what makes the
    filename rows resolve without touching the lookup."""

    def test_lookup_has_no_hardcoded_version_filter(self):
        import inspect
        import cps.progress_syncing.protocols.kosync  # noqa: F401
        mod = sys.modules["cps.progress_syncing.protocols.kosync"]
        src = inspect.getsource(mod.get_book_by_checksum)
        assert "if version is not None" in src, (
            "get_book_by_checksum must only filter by version when the "
            "caller asks — the filename-hash channel (#525/#627) relies on "
            "version-agnostic matching.")


@pytest.mark.unit
class TestBinaryPassNotMaskedByFilenameRows:
    """Greptile finding on PR #636, confirmed: the binary pass's LEFT JOIN
    matched rows of ANY version. Since the filename pass needs no file I/O,
    a book whose file was unreadable at boot got a filename row anyway —
    and every later boot's binary pass then skipped the pair permanently.
    The binary pass must only treat version='koreader' rows as satisfying."""

    @pytest.fixture
    def library_with_absent_file(self, tmp_path, monkeypatch):
        lib = tmp_path / "library"
        lib.mkdir()
        db = sqlite3.connect(lib / "metadata.db")
        db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT)")
        db.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT)")
        db.execute(CHECKSUM_TABLE_SQL)
        # The book's file is NOT on disk yet (mid-move, unreadable mount…).
        db.execute("INSERT INTO books VALUES (1, 'Ghost Book', 'ghost')")
        db.execute("INSERT INTO data VALUES (1, 1, 'EPUB', 'Ghost Book - Author')")
        db.commit()
        db.close()

        cfg = tmp_path / "config"
        cfg.mkdir()
        cdb = sqlite3.connect(cfg / "cwa.db")
        cdb.execute("CREATE TABLE cwa_settings (koreader_sync_enabled INTEGER)")
        cdb.execute("INSERT INTO cwa_settings VALUES (1)")
        cdb.commit()
        cdb.close()
        monkeypatch.setenv("CWA_DB_PATH", str(cfg) + "/")
        return lib

    def _rows(self, library, version):
        conn = sqlite3.connect(library / "metadata.db")
        try:
            return conn.execute(
                "SELECT book FROM book_format_checksums WHERE version=?",
                (version,)).fetchall()
        finally:
            conn.close()

    def test_binary_pass_retries_after_filename_row_exists(
            self, library_with_absent_file):
        import generate_book_checksums as gbc
        lib = library_with_absent_file

        # Boot 1: file absent — binary pass can't hash it, filename pass
        # (DB-only) registers its row regardless.
        gbc.generate_checksums(str(lib), batch_size=10)
        assert self._rows(lib, CHECKSUM_VERSION) == []
        assert self._rows(lib, FILENAME_CHECKSUM_VERSION) == [(1,)]

        # File appears (move completed / mount back).
        (lib / "ghost").mkdir()
        (lib / "ghost" / "Ghost Book - Author.epub").write_bytes(b"z" * 4096)

        # Boot 2: the filename row must NOT satisfy the binary pass —
        # the pair still needs its partial-MD5 computed.
        gbc.generate_checksums(str(lib), batch_size=10)
        assert self._rows(lib, CHECKSUM_VERSION) == [(1,)], (
            "binary pass skipped a pair that only carries a filename-digest "
            "row — its LEFT JOIN must filter on version='koreader'")


@pytest.mark.unit
class TestGetLatestChecksumScopedToBinaryChannel:
    """Source-pin: get_latest_checksum must filter by version (default
    binary 'koreader'). A newer filename-digest row would otherwise shadow
    the binary checksum for any future caller of this exported API."""

    def test_query_filters_on_version(self):
        import inspect
        from cps.progress_syncing.checksums import manager
        src = inspect.getsource(manager.get_latest_checksum)
        assert "version = :version" in src
        sig = inspect.signature(manager.get_latest_checksum)
        assert sig.parameters["version"].default == CHECKSUM_VERSION
