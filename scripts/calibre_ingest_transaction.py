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
from pathlib import Path
import sys
import stat
import struct
import zipfile
import zlib
import xml.etree.ElementTree as ET


class EpubScanBudget:
    """Mutable aggregate limits shared by a caller scanning EPUB candidates."""

    def __init__(self, archive_bytes=200 * 1024 * 1024, expanded_bytes=400 * 1024 * 1024):
        for value in (archive_bytes, expanded_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("EPUB scan quotas must be nonnegative integers")
        self.remaining_archive_bytes = archive_bytes
        self.remaining_expanded_bytes = expanded_bytes


_EPUB_PACKAGE_BYTES = 100 * 1024 * 1024
_EPUB_EXPANDED_BYTES = 200 * 1024 * 1024
_EPUB_MEMBER_BYTES = 50 * 1024 * 1024
_EPUB_ENTRY_COUNT = 10000
_EPUB_RATIO = 1000
_EPUB_CHUNK = 64 * 1024
_EPUB_FRAME = b"CWNG-EPUB-RESOURCES\x00\x01"


def _epub_name(info):
    """Return a safe exact UTF-8 member name, refusing ambiguous ZIP aliases."""
    name = info.filename
    if not isinstance(name, str) or not name or name != info.orig_filename:
        return None
    try:
        encoded = name.encode("utf-8", "strict")
    except UnicodeError:
        return None
    if not (info.flag_bits & 0x800) and any(ord(char) > 127 for char in name):
        return None
    if name.startswith("/") or "\\" in name or "\x00" in name:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        return None
    # Unicode Path extra fields can override the central-directory name.
    # Unknown fields are refused; known timestamp, ZIP64, and platform
    # metadata fields do not alter path interpretation.
    extra = info.extra
    pos = 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            return None
        field, length = struct.unpack_from("<HH", extra, pos)
        pos += 4
        if pos + length > len(extra) or field == 0x7075:
            return None
        if field not in (0x0001, 0x000a, 0x5455, 0x7875):
            return None
        pos += length
    return encoded


def _epub_metadata_bounded(path, physical_size):
    """Count central records before ZipFile allocates their member objects."""
    with open(path, "rb") as stream:
        tail_size = min(physical_size, 65535 + 22)
        stream.seek(physical_size - tail_size)
        tail = stream.read(tail_size)
        position = len(tail)
        while True:
            position = tail.rfind(b"PK\x05\x06", 0, position)
            if position < 0:
                return False
            if position + 22 <= len(tail):
                values = struct.unpack_from("<4s4H2IH", tail, position)
                if position + 22 + values[-1] == len(tail):
                    break
        _, disk, directory_disk, disk_count, count, size, offset, _comment = values
        end = physical_size - tail_size + position
        if (disk or directory_disk or disk_count != count
                or not 0 < count <= _EPUB_ENTRY_COUNT
                or size == 0xffffffff or offset == 0xffffffff
                or offset + size != end):
            return False
        stream.seek(offset)
        records = 0
        consumed = 0
        while consumed < size:
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                return False
            name_length, extra_length, comment_length = struct.unpack_from("<3H", header, 28)
            record_size = 46 + name_length + extra_length + comment_length
            consumed += record_size
            records += 1
            if consumed > size or records > _EPUB_ENTRY_COUNT:
                return False
            stream.seek(record_size - 46, os.SEEK_CUR)
        return consumed == size and records == count


def _epub_local_header(archive, info, name_bytes):
    """Validate local fields and descriptor; return the complete record range."""
    source = archive.fp
    source.seek(info.header_offset)
    header = source.read(30)
    if len(header) != 30:
        return False
    fields = struct.unpack("<IHHHHHIIIHH", header)
    signature, _version, flags, method, _time, _date, crc, csize, usize, nlen, elen = fields
    if signature != 0x04034B50 or flags != info.flag_bits or method != info.compress_type:
        return False
    # Data-descriptor archives may leave these local fields zero. Ordinary
    # headers must agree with the authoritative directory and actual CRC.
    if flags & 8:
        if crc not in (0, info.CRC) or csize not in (0, info.compress_size) or usize not in (0, info.file_size):
            return False
    elif (crc, csize, usize) != (info.CRC, info.compress_size, info.file_size):
        return False
    local_name = source.read(nlen)
    local_extra = source.read(elen)
    if local_name != name_bytes or len(local_extra) != elen:
        return False
    payload_start = source.tell()
    record_end = payload_start + info.compress_size
    pos = 0
    while pos < len(local_extra):
        if pos + 4 > len(local_extra):
            return False
        field, length = struct.unpack_from("<HH", local_extra, pos)
        pos += 4
        if pos + length > len(local_extra) or field == 0x7075:
            return False
        if field not in (0x0001, 0x000a, 0x5455, 0x7875):
            return False
        pos += length
    if flags & 8:
        source.seek(record_end)
        descriptor = source.read(16)
        if len(descriptor) < 12:
            return False
        signed = descriptor[:4] == b"PK\x07\x08"
        offset = 4 if signed else 0
        if len(descriptor) < offset + 12:
            return False
        if struct.unpack_from("<III", descriptor, offset) != (info.CRC, info.compress_size, info.file_size):
            return False
        record_end += offset + 12
    if not 0 <= info.header_offset < payload_start <= record_end <= archive.start_dir:
        return False
    return payload_start, record_end


def _epub_read_resource(stream, info, payload_start, budget):
    """Verify the complete raw stream, rather than ZIP's declared-length view."""
    stream.seek(payload_start)
    remaining = info.compress_size
    decoder = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    pending = b""
    count = 0
    crc = 0
    digest = hashlib.sha256()
    captured = bytearray() if info.filename == "META-INF/container.xml" else None
    while remaining or pending or (decoder is not None and not decoder.eof):
        allowance = min(_EPUB_CHUNK, budget.remaining_expanded_bytes + 1, info.file_size - count + 1)
        if not pending:
            amount = min(_EPUB_CHUNK, remaining)
            if decoder is None:
                amount = min(amount, allowance)
            data = stream.read(amount)
            if len(data) != amount:
                return None
            remaining -= amount
        else:
            data = pending
        if decoder is None:
            chunk = data
            pending = b""
        else:
            try:
                chunk = decoder.decompress(data, allowance)
            except zlib.error:
                # A decoder failure can occur after producing internal output;
                # reserve its bounded maximum so failed candidates consume work.
                budget.remaining_expanded_bytes = max(0, budget.remaining_expanded_bytes - allowance)
                return None
            pending = decoder.unconsumed_tail
        size = len(chunk)
        available = budget.remaining_expanded_bytes
        budget.remaining_expanded_bytes = max(0, available - size)
        count += size
        if size > available or count > info.file_size or count > _EPUB_MEMBER_BYTES:
            return None
        digest.update(chunk)
        crc = zlib.crc32(chunk, crc)
        if captured is not None:
            if count > 2 * 1024 * 1024:
                return None
            captured.extend(chunk)
        if decoder is not None:
            if decoder.unused_data or (decoder.eof and (remaining or pending)):
                return None
            if not chunk and not data and not decoder.eof:
                return None
    if count != info.file_size or crc != info.CRC or (decoder is not None and not decoder.eof):
        return None
    return count, digest.digest(), bytes(captured) if captured is not None else None


def _epub_container_paths(payload):
    """Parse bounded container XML without allowing DTD or entity expansion."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError:
        try:
            text = payload.decode("utf-16")
        except UnicodeError:
            return None
    upper = text.upper()
    ascii_xml = payload.replace(b"\x00", b"").upper()
    if ("<!DOCTYPE" in upper or "<!ENTITY" in upper
            or b"<!DOCTYPE" in ascii_xml or b"<!ENTITY" in ascii_xml):
        return None
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError):
        return None
    ns = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    if root.tag != ns + "container":
        return None
    paths = []
    for item in root.findall(f"{ns}rootfiles/{ns}rootfile"):
        value = item.get("full-path")
        if not value:
            return None
        paths.append(value)
    return paths or None


class _OrdinaryContainerBuilder(ET.TreeBuilder):
    def pi(self, target, text):
        # Processing instructions can affect consumers, including outside the root.
        raise ValueError("unsupported container processing instruction")


def _ordinary_container_identity(payload):
    """Only standard single-rootfile locators, never general XML equivalence."""
    if _epub_container_paths(payload) is None:
        return None
    try:
        root = ET.fromstring(payload, parser=ET.XMLParser(target=_OrdinaryContainerBuilder()))
    except (ET.ParseError, ValueError):
        return None
    ns = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    if root.tag != ns + "container" or root.attrib != {"version": "1.0"} or len(root) != 1:
        return None
    files = root[0]
    if files.tag != ns + "rootfiles" or files.attrib or len(files) != 1:
        return None
    item = files[0]
    if (item.tag != ns + "rootfile" or len(item)
            or set(item.attrib) != {"full-path", "media-type"}
            or item.get("media-type") != "application/oebps-package+xml"
            or not item.get("full-path")):
        return None
    if any((node.text or "").strip(" \t\r\n") or (node.tail or "").strip(" \t\r\n")
           for node in (root, files, item)):
        return None
    return json.dumps(["1.0", item.get("full-path"), item.get("media-type")],
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _epub_framed_digest(payloads, frame):
    digest = hashlib.sha256()
    digest.update(frame)
    for name in sorted(payloads):
        size, member_digest = payloads[name]
        digest.update(struct.pack(">Q", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", size))
        digest.update(member_digest)
    return digest.hexdigest()


def epub_resource_digest(path, *, budget=None):
    """Backward-compatible exact-resource v2 identity."""
    return epub_resource_identities(path, budget=budget).get(2)


def epub_resource_identities(path, *, budget=None):
    """Compute exact v2 and optional ordinary-locator v3 in one bounded ZIP pass.

    The canonical stream frames each UTF-8 path and its uncompressed length,
    then its SHA-256 payload digest, in bytewise path order. Empty directory
    records and ZIP metadata are excluded; ambiguous or unsupported archives
    conservatively have no identity.
    """
    if budget is None:
        budget = EpubScanBudget()
    if not isinstance(budget, EpubScanBudget):
        raise ValueError("budget must be an EpubScanBudget")
    try:
        source = Path(path)
        st = source.lstat()
        if not stat.S_ISREG(st.st_mode):
            return {}
        if st.st_size > budget.remaining_archive_bytes:
            budget.remaining_archive_bytes = 0
            return {}
        budget.remaining_archive_bytes -= st.st_size
        if st.st_size > _EPUB_PACKAGE_BYTES or not _epub_metadata_bounded(source, st.st_size):
            return {}
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _EPUB_ENTRY_COUNT:
                return {}
            if (infos[0].filename != "mimetype" or infos[0].header_offset != 0
                    or infos[0].compress_type != zipfile.ZIP_STORED):
                return {}
            names = set()
            regular = []
            local_records = []
            regular_names = set()
            total = 0
            for info in infos:
                name = _epub_name(info)
                if name is None or name in names:
                    return {}
                names.add(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                directory = info.is_dir()
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    return {}
                if info.flag_bits & ~(0x800 | 8 | 6) or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    return {}
                if directory and (info.file_size or kind == stat.S_IFREG):
                    return {}
                if ((not directory and kind == stat.S_IFDIR) or info.file_size > _EPUB_MEMBER_BYTES
                        or (name == b"META-INF/container.xml" and info.file_size > 2 * 1024 * 1024)):
                    return {}
                if info.compress_size == 0 and info.file_size:
                    return {}
                if info.compress_size and info.file_size > info.compress_size * _EPUB_RATIO:
                    return {}
                total += info.file_size
                if total > _EPUB_EXPANDED_BYTES:
                    return {}
                record = _epub_local_header(archive, info, name)
                if not record:
                    return {}
                local_records.append((info.header_offset, record[1]))
                if not directory:
                    regular_names.add(name.decode("utf-8"))
                regular.append((name, info, record[0], directory))
            if not regular or regular[0][0] != b"mimetype":
                return {}
            end = 0
            for start, record_end in sorted(local_records):
                if start != end:
                    return {}
                end = record_end
            if end != archive.start_dir:
                return {}
            for name, _info, _start, directory in regular:
                parts = name.decode("utf-8").rstrip("/").split("/")
                if (directory and "/".join(parts) in regular_names
                        or any("/".join(parts[:index]) in regular_names for index in range(1, len(parts)))):
                    return {}
            payloads = {}
            container = None
            for name, info, payload_start, directory in regular:
                result = _epub_read_resource(archive.fp, info, payload_start, budget)
                if result is None:
                    return {}
                count, member_digest, captured = result
                if directory:
                    if count:
                        return {}
                    continue
                if name == b"mimetype" and member_digest != hashlib.sha256(b"application/epub+zip").digest():
                    return {}
                if captured is not None:
                    container = captured
                payloads[name] = (count, member_digest)
            if container is None or len(container) > 2 * 1024 * 1024:
                return {}
            rootfiles = _epub_container_paths(container)
            if not rootfiles:
                return {}
            for rootfile in rootfiles:
                try:
                    root_name = rootfile.encode("utf-8", "strict")
                except UnicodeError:
                    return {}
                if root_name not in payloads or b"/" in root_name[:1] or b"\\" in root_name:
                    return {}
            identities = {2: _epub_framed_digest(payloads, _EPUB_FRAME)}
            normalized = _ordinary_container_identity(container)
            if normalized is not None and b"META-INF/signatures.xml" not in payloads:
                payloads[b"META-INF/container.xml"] = (len(normalized), hashlib.sha256(normalized).digest())
                identities[3] = _epub_framed_digest(payloads, b"cwng-epub-ordinary-container-v3\0")
            return identities
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile,
            EOFError, UnicodeError, struct.error, NotImplementedError):
        return {}

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


def stored_format_path(cache, path):
    """Match the receipt verifier's resolved Calibre-library boundary."""
    if not path:
        return None
    try:
        library = Path(cache.backend.library_path).resolve(strict=True)
        stored = Path(path).resolve(strict=True)
        if not stored.is_relative_to(library) or not stored.is_file():
            return None
        return stored
    except (OSError, RuntimeError):
        return None


def stored_format_digest(cache, path):
    stored = stored_format_path(cache, path)
    try:
        return content_digest(stored) if stored is not None else None
    except OSError:
        return None


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


def validate_folder_label_operation(cache, operation):
    """Validate the selected Calibre field before changing any book rows."""
    if operation is None:
        return None
    if not isinstance(operation, dict):
        raise ValueError("invalid ingest folder-label operation")
    target = operation.get("target")
    values = operation.get("values")
    if not isinstance(target, str) or not (target == "tags" or target.startswith("#")):
        raise ValueError("invalid ingest folder-label target")
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("invalid ingest folder-label values")

    metadata = getattr(cache, "field_metadata", None)
    if metadata is None or target not in metadata:
        raise ValueError(f"configured ingest folder-label field {target!r} is unavailable")
    field = metadata[target]
    datatype = field.get("datatype") if hasattr(field, "get") else None
    multiple = field.get("is_multiple") if hasattr(field, "get") else None
    if datatype != "text" or not multiple:
        raise ValueError(f"configured ingest folder-label field {target!r} is not a multivalue text field")
    return target, values


def apply_folder_labels(cache, book_ids, operation, validated=None):
    """Union source-folder values into each affected book's selected field."""
    if operation is None:
        return
    target, values = validated or validate_folder_label_operation(cache, operation)
    if not values:
        return

    changed = {}
    for book_id in sorted({int(value) for value in book_ids}):
        existing = cache.field_for(target, book_id, default_value=[]) or []
        merged = list(existing)
        known = {
            str(value).strip().casefold()
            for value in merged
            if value is not None and str(value).strip()
        }
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in known:
                known.add(key)
                merged.append(value)
        if list(existing) != merged:
            changed[book_id] = merged
    if changed:
        cache.set_field(target, changed)
        cache.dump_metadata(book_ids=changed)


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



def acquisition_result(cache, digest):
    rows = list(cache.backend.execute(
        "SELECT result_json FROM cwng_acquisition_ingest_result WHERE source_sha256=?",
        (digest,),
    ))
    if not rows:
        return None
    values = {row[0] for row in rows}
    if len(values) != 1:
        raise RuntimeError("conflicting acquisition provenance")
    result = json.loads(values.pop())
    if result["source_sha256"] != digest or not result["book_ids"]:
        raise RuntimeError("invalid acquisition provenance")
    if (result.get("disposition") == "existing_retained"
            and result.get("artifact_identity_version") not in (1, 2, 3)):
        # Earlier versions retained title/author matches without proving that
        # the selected artifact was present. Reinspect for future requests;
        # historical application receipts remain an audit of their old import.
        cache.backend.execute("DELETE FROM cwng_acquisition_ingest_result WHERE source_sha256=?", (digest,))
        return None
    for book_id in result["book_ids"]:
        exists = list(cache.backend.execute("SELECT 1 FROM books WHERE id=?", (book_id,)))
        stored_path = cache.format_abspath(book_id, result.get("format", "")) if exists and result.get("format") else None
        stored_digest = stored_format_digest(cache, stored_path)
        current = bool(stored_digest and stored_digest == result["imported_sha256"])
        if not current:
            # Explicit deletion/replacement invalidates this recovery target.
            # Historical appDB receipts remain audit; new jobs must reinspect.
            cache.backend.execute("DELETE FROM cwng_acquisition_ingest_result WHERE source_sha256=?", (digest,))
            return None
    return dict(result, status="already_imported")


def add_acquisition(cache, metadata, extension, path, source_digest):
    """Keep existing editions untouched, including under global overwrite policy.

    Metadata matches are candidates only. Exact prepared bytes are preferred;
    EPUBs may also retain a deterministic book with exactly equal resources
    across ZIP repackaging or ordinary container-locator serialization.
    Different publication resources and unsupported locators stay separate.
    Provenance describes the bytes actually retained/copied, after import plugins.
    """
    candidates = identical_format_paths(cache, metadata, extension)
    prepared_digest = content_digest(path)
    candidate_digests = {}

    def has_prepared_bytes(candidate):
        digest = stored_format_digest(cache, candidate["path"])
        candidate_digests[candidate["book_id"]] = digest
        return digest == prepared_digest

    selected = next((candidate for candidate in candidates if has_prepared_bytes(candidate)), None)
    identity_version = 1
    if selected is None and candidates and extension.lower() == "epub":
        budget = EpubScanBudget()
        prepared_resources = epub_resource_identities(path, budget=budget)
        locator_candidate = None
        if prepared_resources:
            # One shared scan budget; exact resources outrank an earlier locator match.
            for candidate in candidates[:32]:
                stored = stored_format_path(cache, candidate["path"])
                if stored is None or candidate_digests.get(candidate["book_id"]) is None:
                    continue
                current = epub_resource_identities(stored, budget=budget)
                if current.get(2) == prepared_resources[2]:
                    selected = candidate
                    identity_version = 2
                    break
                if (locator_candidate is None and 3 in prepared_resources
                        and current.get(3) == prepared_resources[3]):
                    locator_candidate = candidate
            if selected is None and locator_candidate is not None:
                selected = locator_candidate
                identity_version = 3
    if selected is not None:
        book_ids = {selected["book_id"]}
        imported_digest = candidate_digests[selected["book_id"]]
        disposition = "existing_retained"
    else:
        book_ids, _duplicates = cache.add_books(
            [(metadata, {extension: path})], add_duplicates=True, run_hooks=False
        )
        book_ids = set(book_ids)
        if not book_ids:
            raise RuntimeError("acquisition produced no authoritative book IDs")
        stored_paths = [cache.format_abspath(book_id, extension) for book_id in sorted(book_ids)]
        digests = {stored_format_digest(cache, stored) for stored in stored_paths}
        if None in digests:
            raise RuntimeError("acquisition stored format is unavailable")
        if len(digests) != 1:
            raise RuntimeError("acquisition stored formats differ")
        imported_digest = digests.pop()
        disposition = "imported"
    result = {"status": "imported", "source_sha256": source_digest,
              "imported_sha256": imported_digest, "book_ids": sorted(book_ids),
              "disposition": disposition, "format": extension,
              "artifact_identity_version": identity_version}
    persisted = json.dumps({key: value for key, value in result.items() if key != "status"}, sort_keys=True)
    attach_marker(cache, book_ids, source_digest)
    cache.backend.execute(
        "INSERT INTO cwng_acquisition_ingest_result (source_sha256,result_json) VALUES (?,?)",
        (source_digest, persisted),
    )
    cache.dump_metadata(book_ids=book_ids)
    return result

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
        acquisition = getattr(args, "acquisition", False)
        if args.action == "apply-folder-labels":
            operation = json.loads(args.metadata_json).get("ingest_folder_labels")
            with cache.write_lock, cache.backend.conn:
                validated = validate_folder_label_operation(cache, operation)
                if acquisition:
                    previous = acquisition_result(cache, source_digest)
                    if not previous:
                        raise RuntimeError("folder labels require a committed acquisition receipt")
                    book_ids = previous["book_ids"]
                else:
                    book_ids = sorted(marker_book_ids(cache, source_digest))
                    if not book_ids:
                        raise RuntimeError("folder labels require a committed source marker")
                apply_folder_labels(cache, book_ids, operation, validated)
            return {
                "status": "already_imported",
                "source_sha256": source_digest,
                "imported_sha256": imported_digest,
                "book_ids": sorted(book_ids),
            }
        if acquisition:
            # Private provenance cannot be manufactured by ebook identifiers.
            # SQLite backups retain this additive table; Calibre library export
            # or rebuild may not. A missing record causes safe reinspection.
            with cache.write_lock, cache.backend.conn:
                cache.backend.execute(
                    "CREATE TABLE IF NOT EXISTS cwng_acquisition_ingest_result "
                    "(source_sha256 TEXT PRIMARY KEY NOT NULL, result_json TEXT NOT NULL)"
                )
                previous = acquisition_result(cache, source_digest)
                if previous:
                    folder_labels = json.loads(args.metadata_json).get("ingest_folder_labels")
                    if args.action != "inspect" and folder_labels is not None:
                        validated = validate_folder_label_operation(cache, folder_labels)
                        apply_folder_labels(cache, previous["book_ids"], folder_labels, validated)
                    return previous
        existing = marker_book_ids(cache, source_digest)
        if existing and not acquisition:
            overrides = json.loads(args.metadata_json)
            folder_labels = overrides.get("ingest_folder_labels")
            if args.action != "inspect" and folder_labels is not None:
                with cache.write_lock, cache.backend.conn:
                    validated = validate_folder_label_operation(cache, folder_labels)
                    apply_folder_labels(cache, existing, folder_labels, validated)
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
                if acquisition:
                    previous = acquisition_result(cache, source_digest)
                    if previous:
                        folder_labels = overrides.get("ingest_folder_labels")
                        validated = validate_folder_label_operation(cache, folder_labels)
                        apply_folder_labels(cache, previous["book_ids"], folder_labels, validated)
                        return previous
                    folder_labels = overrides.get("ingest_folder_labels")
                    validated_folder_labels = validate_folder_label_operation(cache, folder_labels)
                    result = add_acquisition(cache, metadata, extension, imported_path, source_digest)
                    apply_folder_labels(
                        cache, result["book_ids"], folder_labels, validated_folder_labels
                    )
                    if args.fail_before_commit:
                        raise RuntimeError("injected failure before transaction commit")
                    return result
                folder_labels = overrides.get("ingest_folder_labels")
                validated_folder_labels = validate_folder_label_operation(cache, folder_labels)
                existing = marker_book_ids(cache, source_digest)
                if existing:
                    result = {"status": "already_imported", "book_ids": sorted(existing)}
                else:
                    added, updated, marked = add_with_automerge(
                        cache, metadata, extension, imported_path, args.automerge, source_digest
                    )
                    result = {
                        "status": "imported",
                        "added_ids": sorted(added),
                        "updated_ids": sorted(updated),
                        "book_ids": sorted(marked),
                    }
                apply_folder_labels(cache, result["book_ids"], folder_labels, validated_folder_labels)
                if args.fail_before_commit:
                    raise RuntimeError("injected failure before transaction commit")
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
    parser.add_argument(
        "--action", choices=("inspect", "import", "apply-folder-labels"), default="import"
    )
    parser.add_argument("--library-path", required=True)
    parser.add_argument("--database-path")
    parser.add_argument("--path", required=True)
    parser.add_argument("--identity-path", required=True)
    parser.add_argument("--expected-import-sha256")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--automerge", choices=("disabled", "ignore", "new_record", "overwrite"), required=True)
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--acquisition", action="store_true")
    parser.add_argument("--fail-before-commit", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        print("CWNG_INGEST_RESULT=" + json.dumps(run(parse_args(sys.argv[1:])), sort_keys=True))
    except Exception as error:
        print(f"CWNG_INGEST_ERROR={error}", file=sys.stderr)
        raise
