#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Import one staged book and atomically mark its source in Calibre's database.

This script is executed with ``calibre-debug -e`` so it uses the exact Calibre
runtime shipped in the image.  Calibre's CLI applies ``--identifier`` only to
new rows; an automerge into an existing row discards it.  The book-row and
identifier changes therefore share one APSW transaction here.  Calibre copies
or replaces format files before that database transaction commits; those
format files are not rolled back if the database transaction fails.  Overwrite
callers preserve the prior file separately for that reason.
"""

import argparse
import hashlib
import json
import os
import sys

from calibre.db.adding import run_import_plugins, run_import_plugins_before_metadata
from calibre.db.legacy import LibraryDatabase
from calibre.db.utils import find_identical_books
from calibre.ebooks.metadata import string_to_authors
from calibre.ebooks.metadata.meta import get_metadata
from calibre.ptempfile import TemporaryDirectory


MARKER_PREFIX = "cwng_ingest_sha256_"


def content_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def marker_type(digest):
    return MARKER_PREFIX + digest


def apply_overrides(metadata, values):
    if values.get("title"):
        metadata.title = str(values["title"])
    if values.get("authors"):
        metadata.authors = string_to_authors(str(values["authors"]))
    for field in ("tags", "languages"):
        value = values.get(field)
        if value:
            setattr(metadata, field, [part.strip() for part in str(value).split(",") if part.strip()])
    if values.get("series"):
        metadata.series = str(values["series"])
        if values.get("series_index") not in (None, ""):
            metadata.series_index = float(values["series_index"])
    supplied_identifiers = values.get("identifiers") or {}
    if supplied_identifiers:
        identifiers = metadata.get_identifiers()
        identifiers.update({str(key): str(value) for key, value in supplied_identifiers.items()})
        metadata.set_identifiers(identifiers)
    cover = values.get("cover")
    if cover and os.path.isfile(cover):
        with open(cover, "rb") as stream:
            metadata.cover_data = ("jpeg", stream.read())
        metadata.cover = None


def prepare_book(path, overrides):
    with TemporaryDirectory("cwng-ingest-add") as temp_dir, run_import_plugins_before_metadata(temp_dir):
        imported_path = run_import_plugins([path])[0]
        extension = os.path.splitext(imported_path)[1].lstrip(".").lower() or "unknown"
        with open(imported_path, "rb") as stream:
            metadata = get_metadata(stream, stream_type=extension, use_libprs_metadata=True)
        if not metadata.title:
            metadata.title = os.path.splitext(os.path.basename(imported_path))[0]
        if not metadata.authors:
            metadata.authors = ["Unknown"]
        apply_overrides(metadata, overrides)
        # The temporary directory must remain alive until Calibre has copied
        # the format, so materialize the operation inside this context.
        yield metadata, extension, imported_path


def marker_book_ids(cache, digest):
    rows = cache.backend.execute(
        "SELECT book FROM identifiers WHERE type=? AND val=?",
        (marker_type(digest), digest),
    )
    return {int(row[0]) for row in rows}


def attach_marker(cache, book_ids, digest):
    field_values = {}
    for book_id in book_ids:
        identifiers = dict(cache.field_for("identifiers", book_id, default_value={}) or {})
        identifiers[marker_type(digest)] = digest
        field_values[book_id] = identifiers
    if field_values:
        cache.set_field("identifiers", field_values)


def identical_format_paths(cache, metadata, extension):
    result = []
    for book_id in sorted(find_identical_books(metadata, cache.data_for_find_identical_books())):
        # ``cache`` is Calibre's new-API Cache, whose 9.11 contract is
        # format_abspath(book_id, fmt).  ``index_is_id`` belongs to legacy
        # database APIs and makes every real overwrite inspection fail here.
        existing = cache.format_abspath(book_id, extension)
        if existing and os.path.isfile(existing):
            result.append({"book_id": int(book_id), "path": existing})
    return result


def add_with_automerge(cache, metadata, extension, path, automerge, digest):
    identical = set(find_identical_books(metadata, cache.data_for_find_identical_books()))
    added_ids, updated_ids = set(), set()
    format_map = {extension: path}

    def add_book():
        ids, _duplicates = cache.add_books(
            [(metadata, format_map)], add_duplicates=True, run_hooks=False
        )
        added_ids.update(ids)

    if automerge != "disabled" and identical:
        needs_add = False
        for book_id in identical:
            book_formats = {value.upper() for value in cache.formats(book_id)}
            incoming_upper = extension.upper()
            if incoming_upper not in book_formats or automerge == "overwrite":
                cache.add_format(book_id, extension, path, replace=True, run_hooks=False)
                updated_ids.add(book_id)
            elif automerge == "new_record":
                needs_add = True
            # ``ignore`` deliberately changes no format. The marker is still
            # attached below so a crash/retry cannot repeat side effects.
        if needs_add:
            add_book()
    elif automerge == "disabled" and identical:
        # Match calibredb's default (no --duplicates): report the duplicate
        # without adding another row.
        pass
    else:
        add_book()

    marker_targets = added_ids | updated_ids
    if not marker_targets:
        marker_targets = identical
    attach_marker(cache, marker_targets, digest)
    cache.dump_metadata(book_ids=marker_targets)
    return added_ids, updated_ids, marker_targets


def run(args):
    imported_digest = content_digest(args.path)
    if args.expected_import_sha256 and imported_digest != args.expected_import_sha256:
        raise RuntimeError("staged import changed after its SHA-256 was computed")
    source_digest = content_digest(args.identity_path)
    if args.expected_source_sha256 and source_digest != args.expected_source_sha256:
        raise RuntimeError("staged source identity changed after its SHA-256 was computed")

    previous_override = os.environ.get("CALIBRE_OVERRIDE_DATABASE_PATH")
    if args.database_path:
        os.environ["CALIBRE_OVERRIDE_DATABASE_PATH"] = args.database_path
    database = None
    try:
        database = LibraryDatabase(args.library_path)
        cache = database.new_api
        existing = marker_book_ids(cache, source_digest)
        if existing:
            return {
                "status": "already_imported",
                "imported_sha256": imported_digest,
                "source_sha256": source_digest,
                "book_ids": sorted(existing),
            }

        overrides = json.loads(args.metadata_json)
        for metadata, extension, imported_path in prepare_book(args.path, overrides):
            if args.action == "inspect":
                return {
                    "status": "inspect",
                    "imported_sha256": imported_digest,
                    "source_sha256": source_digest,
                    "formats": identical_format_paths(cache, metadata, extension),
                }

            # Recheck after metadata/plugin work and under Calibre's write lock.
            # The APSW context makes the database row and source marker atomic.
            # Calibre's format-file copy/replace is a filesystem side effect and
            # is deliberately not described as part of that transaction.
            with cache.write_lock, cache.backend.conn:
                existing = marker_book_ids(cache, source_digest)
                if existing:
                    result = {"status": "already_imported", "book_ids": sorted(existing)}
                else:
                    added, updated, marked = add_with_automerge(
                        cache, metadata, extension, imported_path, args.automerge, source_digest
                    )
                    if args.fail_before_commit:
                        raise RuntimeError("injected failure before transaction commit")
                    result = {
                        "status": "imported",
                        "added_ids": sorted(added),
                        "updated_ids": sorted(updated),
                        "book_ids": sorted(marked),
                    }
                result["imported_sha256"] = imported_digest
                result["source_sha256"] = source_digest
                return result
    finally:
        if database is not None:
            database.close()
        if args.database_path:
            if previous_override is None:
                os.environ.pop("CALIBRE_OVERRIDE_DATABASE_PATH", None)
            else:
                os.environ["CALIBRE_OVERRIDE_DATABASE_PATH"] = previous_override


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("inspect", "import"), default="import")
    parser.add_argument("--library-path", required=True)
    parser.add_argument("--database-path")
    parser.add_argument("--path", required=True)
    parser.add_argument("--identity-path", required=True)
    parser.add_argument("--expected-import-sha256")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--automerge", choices=("disabled", "ignore", "new_record", "overwrite"), required=True)
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--fail-before-commit", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        print("CWNG_INGEST_RESULT=" + json.dumps(run(parse_args(sys.argv[1:])), sort_keys=True))
    except Exception as error:
        print(f"CWNG_INGEST_ERROR={error}", file=sys.stderr)
        raise
