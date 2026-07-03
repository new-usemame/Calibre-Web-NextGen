#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Generate Partial MD5 Checksums for Existing Books

This utility script generates KOReader-compatible partial MD5 checksums for all
books in a Calibre library that don't already have checksums stored. This runs
on every boot (via cwa-checksum-backfill service) to backfill any missing checksums
for newly added books.

Usage:
    python generate_book_checksums.py [--library-path /path/to/calibre/library] [--books-path /path/to/books] [--force]

Options:
    --library-path  Path to Calibre library directory (defaults to /calibre-library)
    --books-path    Path to books directory (defaults to config_calibre_split_dir setting with --library-path fallback)
    --force         Regenerate checksums even if they already exist
    --batch-size    Number of books to process before committing (default: 100)
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# cps imports are LAZY (moved inside generate_checksums()) so the
# disabled-path early-exit doesn't pay the full cps/__init__.py boot
# cost (~1.5s locally, 30+s under CI worker contention). Importing
# cps.progress_syncing.* triggers cps/__init__.py which loads Flask,
# SQLAlchemy, plugins, the logger, config_sql, ub, db, etc. — all
# unnecessary work when KOReader sync is disabled and the script
# will early-exit. See deflake context in
# tests/unit/test_generate_checksums.py timeout comment.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _check_koreader_sync_enabled_lightweight() -> bool:
    """Read the koreader_sync_enabled setting from cwa.db without
    importing any cps modules. Used by the script's early-exit path
    so a disabled config doesn't pay the cps boot cost.

    Mirrors the semantics of cps.progress_syncing.settings
    .is_koreader_sync_enabled() — which calls CWA_DB().cwa_settings
    and returns bool(settings.get('koreader_sync_enabled', 0)) —
    but reads cwa.db directly via sqlite3 to avoid pulling in cps.

    Honors the CWA_DB_PATH env var (same convention as scripts/cwa_db.py
    after the T1 deflake fix), so test isolation works.

    Falls back to False on any error — matches the production
    is_koreader_sync_enabled() fail-closed behavior.
    """
    cwa_db_dir = os.environ.get("CWA_DB_PATH", "/config/")
    if not cwa_db_dir.endswith("/"):
        cwa_db_dir += "/"
    cwa_db_path = cwa_db_dir + "cwa.db"
    if not os.path.isfile(cwa_db_path):
        return False
    conn = None
    try:
        conn = sqlite3.connect(cwa_db_path, timeout=5)
        cur = conn.cursor()
        row = cur.execute(
            "SELECT koreader_sync_enabled FROM cwa_settings LIMIT 1"
        ).fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()


def _flush_batch(metadata_db: str, batch_rows):
    if not batch_rows:
        return
    try:
        conn = sqlite3.connect(metadata_db, timeout=30)
        cur = conn.cursor()
        cur.executemany(
            '''
            INSERT INTO book_format_checksums (book, format, checksum, version, created)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM book_format_checksums
                WHERE book = ? AND format = ? AND checksum = ?
            )
            ''',
            batch_rows
        )
        conn.commit()
    finally:
        conn.close()


def generate_checksums(library_path: str, books_path: str = None, force: bool = False, batch_size: int = 100):
    """Generate checksums for all books in the library

    Args:
        library_path: Path to Calibre library directory (contains metadata.db)
        books_path: Path to books directory (if different from library_path in split mode)
        force: If True, regenerate checksums even if they exist
        batch_size: Number of books to process before committing
    """
    # Lightweight, no-cps-import check first. The disabled-path returns
    # without ever touching cps/__init__.py, keeping subprocess startup
    # well under a second.
    if not _check_koreader_sync_enabled_lightweight():
        print("KOReader sync is disabled; skipping checksum generation.")
        return

    # Now we know we're going to do real work — pay the heavier cps
    # import. This triggers cps/__init__.py (Flask, SQLAlchemy, plugins,
    # etc.), but the cost is amortized over actual checksum computation.
    from cps.progress_syncing.checksums import (  # noqa: E402
        calculate_koreader_partial_md5,
        calculate_koreader_filename_md5,
        CHECKSUM_VERSION,
        FILENAME_CHECKSUM_VERSION,
    )

    metadata_db = os.path.join(library_path, 'metadata.db')

    if not os.path.exists(metadata_db):
        print(f"ERROR: Calibre database not found at {metadata_db}")
        sys.exit(1)

    # Use books_path if provided and valid, otherwise fall back to library_path
    base_path = books_path if (books_path and os.path.exists(books_path)) else library_path

    print(f"Connecting to Calibre library at: {library_path}")
    if base_path != library_path:
        print(f"Books path (split library mode): {base_path}")
    else:
        print(f"Books path: {base_path}")
    print(f"Force regenerate: {force}")
    print(f"Batch size: {batch_size}")
    print(f"Checksum version: {CHECKSUM_VERSION}")
    print()

    try:
        # Read missing formats without holding the DB open during checksum computation
        conn = sqlite3.connect(metadata_db, timeout=30)
        cur = conn.cursor()

        if force:
            query = '''
                SELECT b.id, b.path, b.title, d.format, d.name
                FROM books b
                JOIN data d ON b.id = d.book
                ORDER BY b.id
            '''
            formats = cur.execute(query).fetchall()
        else:
            # Only a binary-channel row satisfies the binary pass. Filename
            # rows (version 'koreader_filename') are created without file
            # I/O, so an any-version join would permanently skip books whose
            # files were unreadable when the filename pass first ran.
            query = '''
                SELECT b.id, b.path, b.title, d.format, d.name
                FROM books b
                JOIN data d ON b.id = d.book
                LEFT JOIN book_format_checksums bfc ON (
                    bfc.book = b.id
                    AND bfc.format = d.format
                    AND bfc.version = ?
                )
                WHERE bfc.id IS NULL
                ORDER BY b.id
            '''
            formats = cur.execute(query, (CHECKSUM_VERSION,)).fetchall()
    except sqlite3.Error as e:
        print(f"ERROR: Database error: {e}")
        sys.exit(1)
    finally:
        conn.close()

    total = len(formats)

    if total == 0:
        print("✓ All books already have binary checksums!")
        # The filename-hash pass still has to run: pairs with binary rows
        # can be missing their filename digest (fork #525 / #627).
        filename_queued = _backfill_filename_hashes(
            metadata_db, calculate_koreader_filename_md5,
            FILENAME_CHECKSUM_VERSION, batch_size)
        print(f"  Filename hashes: {filename_queued}")
        return

    print(f"Found {total} book format(s) to process\n")

    processed = 0
    queued = 0
    failed = 0
    skipped = 0
    batch_rows = []

    for book_id, book_path, title, format_ext, format_name in formats:
        processed += 1

        file_path = os.path.join(base_path, book_path, f"{format_name}.{format_ext.lower()}")

        if not os.path.exists(file_path):
            print(f"[{processed}/{total}] SKIP: File not found - {title} ({format_ext})")
            skipped += 1
            continue

        checksum = calculate_koreader_partial_md5(file_path)

        if checksum:
            created = datetime.now(timezone.utc).isoformat()
            fmt = format_ext.upper()
            batch_rows.append((book_id, fmt, checksum, CHECKSUM_VERSION, created, book_id, fmt, checksum))
            queued += 1

            if queued % batch_size == 0:
                _flush_batch(metadata_db, batch_rows)
                batch_rows = []
                print(f"  → Committed {queued} checksums to database")
        else:
            print(f"[{processed}/{total}] FAIL: Could not generate checksum - {title} ({format_ext})")
            failed += 1

    if batch_rows:
        _flush_batch(metadata_db, batch_rows)

    # Filename-hash pass (fork #525 / #627): register the digest of the
    # OPDS/Kobo export basename for every (book, format) pair missing one,
    # so clients in 'filename' document-matching mode resolve books. Runs
    # independently of the binary pass above — pairs that already carry a
    # binary row (skipped by the any-row LEFT JOIN) still need this.
    # No file I/O: the digest is derived from database fields alone.
    filename_queued = _backfill_filename_hashes(
        metadata_db, calculate_koreader_filename_md5,
        FILENAME_CHECKSUM_VERSION, batch_size)

    print()
    print("=" * 60)
    print("Summary:")
    print(f"  Total processed: {processed}")
    print(f"  Queued:          {queued}")
    print(f"  Failed:          {failed}")
    print(f"  Skipped:         {skipped}")
    print(f"  Filename hashes: {filename_queued}")
    print("=" * 60)


def _backfill_filename_hashes(metadata_db, digest_fn, version, batch_size=100):
    """Insert missing 'koreader_filename' checksum rows.

    The hashed string is ``data.name + "." + lower(data.format)`` — the
    basename CW-NG serves on OPDS/Kobo downloads and the on-disk library
    basename, which is what a device in filename-matching mode hashes.
    Returns the number of rows queued for insert.
    """
    try:
        conn = sqlite3.connect(metadata_db, timeout=30)
        cur = conn.cursor()
        missing = cur.execute('''
            SELECT b.id, d.format, d.name
            FROM books b
            JOIN data d ON b.id = d.book
            LEFT JOIN book_format_checksums bfc ON (
                bfc.book = b.id
                AND bfc.format = d.format
                AND bfc.version = ?
            )
            WHERE bfc.id IS NULL
            ORDER BY b.id
        ''', (version,)).fetchall()
    except sqlite3.Error as e:
        print(f"WARNING: filename-hash pass could not read library: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()

    if not missing:
        return 0

    print(f"\nFilename-hash pass: {len(missing)} format(s) missing a filename digest")

    queued = 0
    batch_rows = []
    for book_id, format_ext, format_name in missing:
        digest = digest_fn(f"{format_name}.{format_ext.lower()}")
        if not digest:
            continue
        fmt = format_ext.upper()
        created = datetime.now(timezone.utc).isoformat()
        batch_rows.append((book_id, fmt, digest, version, created,
                           book_id, fmt, digest))
        queued += 1
        if len(batch_rows) >= batch_size:
            _flush_batch(metadata_db, batch_rows)
            batch_rows = []

    if batch_rows:
        _flush_batch(metadata_db, batch_rows)

    print(f"Filename-hash pass: committed {queued} digest(s)")
    return queued


def get_books_path():
    """
    Get the split library books path from app.db if split mode is enabled.
    
    Returns:
        The books path from config_calibre_split_dir if it exists and is valid,
        otherwise None to indicate the library path should be used.
    """
    try:
        conn = sqlite3.connect("/config/app.db", timeout=30)
        cur = conn.cursor()

        # Check if split mode is enabled and get split path
        result = cur.execute('SELECT config_calibre_split, config_calibre_split_dir FROM settings LIMIT 1;').fetchone()
        
        if not result:
            return None
            
        split_enabled, split_path = result
        
        # Only return split path if split mode is enabled, path is not NULL, and path exists
        if split_enabled and split_path and os.path.exists(split_path):
            return split_path
            
        return None

    except sqlite3.Error as e:
        # Log warning but don't crash - fall back to library path
        print(f"WARNING: Could not read split library setting from app.db: {e}")
        print(f"WARNING: Falling back to --library-path for books location")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Generate KOReader sync checksums for books in Calibre library',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--library-path',
        default='/calibre-library',
        help='Path to Calibre library directory (default: /calibre-library)'
    )

    parser.add_argument(
        '--books-path',
        default=get_books_path(),
        help='Path to books directory (default: config_calibre_split_dir setting or --library-path)'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Regenerate checksums even if they already exist'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Number of books to process before committing (default: 100)'
    )

    args = parser.parse_args()

    # Validate library path
    if not os.path.isdir(args.library_path):
        print(f"ERROR: Library path does not exist: {args.library_path}")
        sys.exit(1)

    try:
        generate_checksums(args.library_path, args.books_path, args.force, args.batch_size)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(130)


if __name__ == '__main__':
    main()
