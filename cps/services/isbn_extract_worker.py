# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone parser child for isbn_extract.py (no application imports)."""
from __future__ import annotations

import json
import os
import posixpath
import re
import stat
import sys
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote

MAX_FORMATS = 6
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ZIP_RATIO = 200
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_TEXT_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TEXT_TOTAL_BYTES = 24 * 1024 * 1024
MAX_DOCUMENTS = 250
MAX_PDF_PAGES = 250
MAX_PDF_PAGE_TEXT = 256 * 1024
MAX_CANDIDATES = 20
MAX_CONTEXT_CHARS = 220

_ISBN_RE = re.compile(r"(?<![0-9])(?P<isbn>97[89](?:[\s\u00a0.\-‐‑‒–—]?[0-9]){10})(?![0-9])")
_TEXT_EXTENSIONS = {".html", ".htm", ".xhtml", ".txt"}
_CONTAINER = "META-INF/container.xml"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF_NS = "http://www.idpf.org/2007/opf"


def _set_resource_limits():
    if os.name != "posix":
        raise RuntimeError("resource_limits_unavailable")
    import resource

    # Do not parse hostile documents unless both CPU and address-space limits
    # were installed. The parent timeout is a second bound, not a substitute
    # for the memory/CPU limits on supported POSIX deployments.
    resource.setrlimit(resource.RLIMIT_CPU, (6, 7))
    memory_limit = 768 * 1024 * 1024
    memory_resource = getattr(resource, "RLIMIT_AS", getattr(resource, "RLIMIT_VMEM", None))
    if memory_resource is None:
        raise RuntimeError("address_space_limit_unavailable")
    resource.setrlimit(memory_resource, (memory_limit, memory_limit))
    if hasattr(resource, "RLIMIT_CORE"):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _valid_isbn13(value):
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) != 13 or not digits.startswith(("978", "979")):
        return None
    checksum = sum(int(digit) * (1 if index % 2 == 0 else 3)
                   for index, digit in enumerate(digits[:12]))
    if (10 - checksum % 10) % 10 != int(digits[-1]):
        return None
    return digits


def _context(text, start, end, location):
    left = max(0, start - 80)
    right = min(len(text), end + 80)
    snippet = " ".join(text[left:right].split())
    if len(snippet) > MAX_CONTEXT_CHARS:
        snippet = snippet[:MAX_CONTEXT_CHARS - 1].rstrip() + "…"
    return f"{location}: {snippet}" if location else snippet


def _scan_text(text, fmt, location, candidates):
    for match in _ISBN_RE.finditer(text):
        isbn = _valid_isbn13(match.group("isbn"))
        if not isbn or isbn in candidates:
            continue
        if len(candidates) >= MAX_CANDIDATES:
            return True
        candidates[isbn] = {
            "isbn": isbn,
            "context": _context(text, match.start(), match.end(), location),
            "format": fmt.lower(),
        }
    return False


def _safe_archive_name(name):
    if (not isinstance(name, str) or not name or "\\" in name
            or name.startswith("/") or "\x00" in name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in name.split("/"))


def _read_member(archive, info, max_bytes):
    if info.file_size > max_bytes:
        return None
    chunks = []
    total = 0
    with archive.open(info, "r") as source:
        while total <= max_bytes:
            chunk = source.read(min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
    return b"".join(chunks)


def _xhtml_text(raw):
    from lxml import etree, html

    # XHTML can be XML or real-world-recoverable HTML. Neither parser is
    # allowed to resolve external resources or expand DTD entities.
    parser = html.HTMLParser(no_network=True, recover=True, huge_tree=False, remove_comments=True)
    root = html.fromstring(raw, parser=parser)
    for node in root.xpath("//script|//style|//noscript|//svg|//head"):
        node.drop_tree()
    return " ".join(part for part in root.itertext() if part and part.strip())


def _epub_document_order(archive, infos):
    by_name = {info.filename: info for info in infos}
    try:
        container_info = by_name.get(_CONTAINER)
        if container_info is None:
            return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True
        raw = _read_member(archive, container_info, MAX_PACKAGE_BYTES)
        if raw is None:
            return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True

        from lxml import etree
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                                 huge_tree=False, recover=False)
        tree = etree.fromstring(raw, parser=parser)
        opf_paths = tree.xpath(
            "./c:rootfiles/c:rootfile/@full-path",
            namespaces={"c": _CONTAINER_NS},
        )
        if not opf_paths or not _safe_archive_name(opf_paths[0]):
            return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True
        opf_info = by_name.get(opf_paths[0])
        if opf_info is None:
            return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True
        opf_raw = _read_member(archive, opf_info, MAX_PACKAGE_BYTES)
        if opf_raw is None:
            return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True
        opf = etree.fromstring(opf_raw, parser=parser)
        manifest = {}
        for item in opf.xpath("./opf:manifest/opf:item", namespaces={"opf": _OPF_NS}):
            item_id = item.get("id")
            href = unquote(item.get("href") or "")
            media_type = item.get("media-type", "").lower()
            if not item_id or not href:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(opf_paths[0]), href))
            if not _safe_archive_name(resolved):
                continue
            manifest[item_id] = (resolved, media_type)
        ordered = []
        for itemref in opf.xpath("./opf:spine/opf:itemref", namespaces={"opf": _OPF_NS}):
            entry = manifest.get(itemref.get("idref"))
            if not entry:
                continue
            name, media_type = entry
            info = by_name.get(name)
            if info and (media_type in {"application/xhtml+xml", "text/html", "text/plain"}
                         or PurePosixPath(name).suffix.lower() in _TEXT_EXTENSIONS):
                ordered.append(info)
        seen = {info.filename for info in ordered}
        ordered.extend(
            info for info in infos
            if info.filename not in seen
            and PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS
        )
        return ordered, False
    except Exception:
        return [info for info in infos if PurePosixPath(info.filename).suffix.lower() in _TEXT_EXTENSIONS], True


def _sample_order(items, front=10, back=5):
    indexes = list(range(min(front, len(items))))
    indexes.extend(index for index in range(len(items) - 1, max(len(items) - back - 1, front - 1), -1)
                   if index not in indexes)
    indexes.extend(index for index in range(len(items)) if index not in indexes)
    return indexes


def _scan_epub(stream, fmt, candidates):
    truncated = False
    text_total = 0
    with zipfile.ZipFile(stream, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            return True
        seen = set()
        uncompressed = 0
        for info in infos:
            checked_name = info.filename[:-1] if info.is_dir() and info.filename.endswith("/") else info.filename
            if (not _safe_archive_name(checked_name) or info.orig_filename != info.filename
                    or info.filename in seen):
                raise ValueError("invalid_archive_directory")
            seen.add(info.filename)
            if info.is_dir():
                continue
            uncompressed += info.file_size
            if uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("archive_expansion_limit")
            if (info.file_size > 1024 * 1024 and info.compress_size > 0
                    and info.file_size / info.compress_size > MAX_ZIP_RATIO):
                raise ValueError("archive_ratio_limit")

        documents, bad_package = _epub_document_order(archive, infos)
        truncated = bad_package or len(documents) > MAX_DOCUMENTS
        # Select from the complete spine order before applying the scan cap so
        # a long book still samples its actual final chapters.
        ordered = [documents[index] for index in _sample_order(documents)[:MAX_DOCUMENTS]]
        for ordinal, info in enumerate(ordered, start=1):
            if text_total >= MAX_TEXT_TOTAL_BYTES:
                return True
            raw = _read_member(archive, info, MAX_TEXT_MEMBER_BYTES)
            if raw is None:
                truncated = True
                continue
            text_total += len(raw)
            if text_total > MAX_TEXT_TOTAL_BYTES:
                return True
            suffix = PurePosixPath(info.filename).suffix.lower()
            try:
                if suffix == ".txt":
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = raw.decode("latin-1")
                else:
                    text = _xhtml_text(raw)
            except Exception:
                truncated = True
                continue
            location = f"Chapter {PurePosixPath(info.filename).name or ordinal}"
            if _scan_text(text, fmt, location, candidates):
                return True
    return truncated


def _scan_pdf(stream, fmt, candidates):
    from pypdf import PdfReader

    reader = PdfReader(stream, strict=False)
    page_count = len(reader.pages)
    indexes = list(range(min(10, page_count)))
    back_start = max(page_count - 5, len(indexes))
    indexes.extend(index for index in range(page_count - 1, back_start - 1, -1)
                   if index not in indexes)
    for index in range(page_count):
        if len(indexes) >= MAX_PDF_PAGES:
            break
        if index not in indexes:
            indexes.append(index)
    truncated = page_count > len(indexes)
    for index in indexes:
        page_text = reader.pages[index].extract_text() or ""
        if len(page_text) > MAX_PDF_PAGE_TEXT:
            page_text = page_text[:MAX_PDF_PAGE_TEXT]
            truncated = True
        if _scan_text(page_text, fmt, f"Page {index + 1}", candidates):
            return True
    return truncated


def _scan_txt(stream, fmt, candidates):
    raw = stream.read(MAX_TEXT_TOTAL_BYTES + 1)
    truncated = len(raw) > MAX_TEXT_TOTAL_BYTES
    raw = raw[:MAX_TEXT_TOTAL_BYTES]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return _scan_text(text, fmt, "Text", candidates) or truncated


def _scan_one(item, candidates):
    fmt = item["format"]
    fd = item["fd"]
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > MAX_INPUT_BYTES:
        return False, True
    with os.fdopen(os.dup(fd), "rb") as stream:
        if fmt in {"EPUB", "KEPUB"}:
            return True, _scan_epub(stream, fmt, candidates)
        if fmt == "PDF":
            return True, _scan_pdf(stream, fmt, candidates)
        if fmt == "TXT":
            return True, _scan_txt(stream, fmt, candidates)
    return False, True


def main():
    try:
        _set_resource_limits()
    except BaseException:
        sys.stdout.write(json.dumps({
            "available": False,
            "candidates": [],
            "scanned_formats": [],
            "failed_formats": [],
            "truncated": True,
            "sandboxed": False,
        }, separators=(",", ":")))
        return
    try:
        payload = sys.stdin.buffer.read(8193)
        if len(payload) > 8192:
            raise ValueError("request_too_large")
        request = json.loads(payload)
        formats = request.get("formats")
        if not isinstance(formats, list) or not formats or len(formats) > MAX_FORMATS:
            raise ValueError("invalid_formats")
        candidates = {}
        scanned = []
        failed = []
        truncated = False
        for item in formats:
            if (not isinstance(item, dict) or type(item.get("fd")) is not int
                    or item.get("format") not in {"EPUB", "KEPUB", "PDF", "TXT"}):
                raise ValueError("invalid_format")
            try:
                was_scanned, was_truncated = _scan_one(item, candidates)
            except Exception:
                was_scanned, was_truncated = False, True
            if was_scanned:
                scanned.append(item["format"].lower())
            else:
                failed.append(item["format"].lower())
            truncated = truncated or was_truncated
        output = {
            "available": bool(scanned),
            "candidates": list(candidates.values()),
            "scanned_formats": scanned,
            "failed_formats": failed,
            "truncated": truncated,
            "sandboxed": True,
        }
    except BaseException:
        output = {"available": False, "candidates": [], "scanned_formats": [],
                  "failed_formats": [], "truncated": True, "sandboxed": True}
    sys.stdout.write(json.dumps(output, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
