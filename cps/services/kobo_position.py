# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kobo Bookmark → EPUB CFI converter (production port of the
99.3%-round-trip-proven prototype from the 2026-05-17 borrow-day
session).

See ``notes/KOBO-WEB-READER-ANNOTATIONS-DESIGN.md`` §3.5 for the
algorithm rationale and §3.6 for the round-trip evidence. This module
is the H1 Phase 2 deliverable; P3's import endpoint calls
:func:`compute_cfi_range` per highlight at ingest time, P5's web-reader
JS consumes the resulting CFI strings via ``epub.js``'s
``rendition.annotations.highlight(cfi, ...)``.

Production hardening over the prototype:

* **DOM parser instead of regex** — `lxml.html.fromstring` plus
  XPath lookups replace the brittle ``<span[^>]*id=...>`` regex that
  produced the prototype's lone 0.7% failure on nested spans.
* **Per-EPUB cache** — parsing a 30 KB chapter HTML on every one of a
  book's 100+ highlights is wasteful; the spine + per-chapter parsed
  tree are cached behind ``functools.lru_cache`` keyed by EPUB path +
  mtime so a re-uploaded book invalidates automatically.
* **ContextString fallback** — when CFI resolution fails (for example
  the EPUB was re-uploaded with a different KoboSpan ID layout), the
  module re-anchors to the surrounding text snippet that Kobo stores
  in ``Bookmark.ContextString`` — a degraded match that still puts the
  highlight in the right paragraph.
* **Plain EPUB (no KoboSpan IDs)** — when ``StartContainerChildIndex``
  is not the ``-99`` selector sentinel, the module falls back to
  DOM-index walking via ``StartContainerChildIndex`` instead of the
  KoboSpan ``id`` lookup.

The public surface is intentionally narrow — :func:`compute_cfi_range`
plus :func:`parse_spine`. Internal helpers are underscore-prefixed and
not part of the import-contract.
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from lxml import html as lxml_html

log = logging.getLogger(__name__)

# OPF / container.xml namespace constants. ElementTree expects the URL
# explicitly because no XML default-namespace handling.
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}
_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}

# Sentinel value Kobo writes in ``StartContainerChildIndex`` when the
# corresponding ``StartContainerPath`` is a CSS selector (kepub case)
# rather than a DOM-index walk path (plain EPUB case). Discovered
# empirically against 145 real highlights from the tester's Animal Farm
# kepub (see design doc §3.6 finding 2).
KOBO_SELECTOR_SENTINEL = -99

# A path that resembles ``span#kobo\\.4\\.1`` — Kobo escapes the dots
# because the source format is a literal CSS selector. The capturing
# group preserves the un-escaped id form (``kobo.4.1``).
_KOBOSPAN_PATH_RE = re.compile(r"#([\w.\\-]+)$")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KoboPosition:
    """One Kobo highlight's position fields, exactly as they live in the
    ``KoboReader.sqlite.Bookmark`` table (see
    ``notes/KOBO-PROTOCOL-REFERENCE.md`` §10.1)."""

    content_id: str                       # "<book_uuid>!!<chapter_file>"
    start_container_path: str
    start_container_child_index: Optional[int]
    start_offset: int
    end_container_path: str
    end_container_child_index: Optional[int]
    end_offset: int
    context_string: Optional[str] = None  # for fallback re-anchoring


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_cfi_range(epub_path: Path, position: KoboPosition) -> Optional[str]:
    """Return an ``epubcfi(...)`` string anchoring ``position`` inside
    the EPUB at ``epub_path``, or ``None`` if the position cannot be
    resolved even with the ContextString fallback.

    The CFI is consumable directly by epub.js's
    ``rendition.annotations.highlight(cfi, ...)`` — no further
    transformation needed at the JS layer.

    The function never raises on a malformed position; degraded inputs
    log a warning and return ``None`` so the import path can record the
    raw position fields (for re-anchoring later) without dropping the
    highlight entirely.
    """
    if not isinstance(epub_path, Path):
        epub_path = Path(epub_path)
    if not epub_path.is_file():
        log.warning("compute_cfi_range: %s does not exist", epub_path)
        return None
    if not position.content_id or "!!" not in position.content_id:
        log.warning(
            "compute_cfi_range: malformed content_id %r — expected '<uuid>!!<chapter>'",
            position.content_id,
        )
        return None

    chapter_file = position.content_id.split("!!", 1)[1]
    cache_key = (str(epub_path), epub_path.stat().st_mtime_ns)
    try:
        spine = _get_spine(cache_key, epub_path)
    except Exception as e:
        log.warning("compute_cfi_range: spine parse failed for %s: %s", epub_path, e)
        return None
    if not spine:
        log.warning("compute_cfi_range: empty spine for %s", epub_path)
        return None

    spine_index = _resolve_spine_index(spine, chapter_file)
    if spine_index is None:
        log.warning(
            "compute_cfi_range: chapter %r not in spine of %s",
            chapter_file, epub_path,
        )
        return None
    spine_step = f"/6/{2 * (spine_index + 1)}"

    start_id = _extract_kobospan_id(position.start_container_path)
    end_id = _extract_kobospan_id(position.end_container_path)

    if start_id and end_id:
        # The kepub path — KoboSpan IDs present. Walk the chapter DOM to
        # produce a structurally valid, portable source-document CFI
        # (proper 3-part range, offsets on text nodes). The KoboSpan id is
        # the reliable anchor whenever it's present; child_index is only
        # consulted for plain EPUBs below. (Kobo writes child_index=-99 in
        # KoboReader.sqlite, but the live reading-services PATCH omits it,
        # so live-captured annotations store NULL — gating the selector
        # path on child_index==-99 broke every live capture, found via
        # real-device test 2026-05-24.)
        #
        # The web reader does NOT consume this CFI — epub.js injects
        # wrapper divs at render time, so it regenerates a wrapper-aware
        # CFI client-side from the KoboSpan id (annotations.js). This
        # string is for export portability / spec-compliant resolvers.
        try:
            tree = _get_chapter_dom(cache_key, epub_path, chapter_file)
            if tree is not None:
                cfi = _kepub_range_cfi(
                    tree, spine_step, start_id, end_id,
                    position.start_offset, position.end_offset,
                )
                if cfi:
                    return cfi
        except Exception as e:
            log.warning(
                "compute_cfi_range: kepub DOM walk failed for %s::%s — %s",
                epub_path, chapter_file, e,
            )
        # KoboSpan ids present but unresolvable in the DOM (re-uploaded
        # book with a different layout?) — fall through to context.
        return _fallback_via_context(
            epub_path, cache_key, chapter_file, spine_step, position,
        )

    # Plain-EPUB fallback: no KoboSpan IDs to anchor on. Walk by child
    # index instead. The CFI step encoding for child-index walks is
    # ``/2N`` for the Nth element child (1-indexed), even-only so odd
    # steps stay reserved for text nodes.
    start_step = _child_index_to_cfi_step(position.start_container_child_index)
    end_step = _child_index_to_cfi_step(position.end_container_child_index)
    if start_step is None or end_step is None:
        # Neither selector nor child-index — last resort, re-anchor via
        # context_string against the parsed chapter DOM.
        return _fallback_via_context(
            epub_path, cache_key, chapter_file, spine_step, position,
        )

    return f"epubcfi({spine_step}!{start_step}:{position.start_offset},{end_step}:{position.end_offset})"


# Reading progress has only a chapter and span id, with no highlight end or
# character offset. Resolve its span START, and never use the context fallback.
# These limits bound optional work, including compressed XML bombs. The caller
# must run filesystem access off the request thread with a deadline.
MAX_RESUME_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_RESUME_XML_BYTES = 2 * 1024 * 1024
MAX_RESUME_DIRECTORY_BYTES = 256 * 1024
MAX_RESUME_DIRECTORY_ENTRIES = 2048
MAX_RESUME_HREF_CHARS = 2048
# Conservative subset that epub.js can consume without assertion escaping.
# Valid XHTML ids outside this set deliberately retain percentage resume.
_RESUME_ASSERTION_ID = re.compile(r"[A-Za-z0-9_.-]+")


def _resume_directory_start(raw):
    """Bound actual central-directory records BEFORE ZipFile allocates ZipInfo.

    Accept only a conventional single-disk directory with a matching EOCD.
    Reject ZIP64 directory overrides, concatenated archives and malformed size
    claims rather than letting ZipFile reinterpret the bounds we checked.
    """
    import struct

    end = raw.rfind(b"PK\x05\x06", max(0, len(raw) - 65557))
    if end < 0 or end + 22 > len(raw):
        raise ValueError("missing resume ZIP directory")
    disk, start_disk, disk_count, count, size, start, comment = struct.unpack_from(
        "<4H2IH", raw, end + 4)
    if (disk or start_disk or disk_count != count
            or count > MAX_RESUME_DIRECTORY_ENTRIES
            or size > MAX_RESUME_DIRECTORY_BYTES
            or start + size != end or end + 22 + comment != len(raw)
            or raw[max(0, end - 20):end - 16] == b"PK\x06\x07"):
        raise ValueError("unsupported or oversized resume ZIP directory")
    cursor, actual = start, 0
    while cursor < end:
        if cursor + 46 > end or raw[cursor:cursor + 4] != b"PK\x01\x02":
            raise ValueError("malformed resume ZIP directory record")
        name, extra, entry_comment = struct.unpack_from("<3H", raw, cursor + 28)
        cursor += 46 + name + extra + entry_comment
        actual += 1
        if cursor > end or actual > MAX_RESUME_DIRECTORY_ENTRIES:
            raise ValueError("resume ZIP directory exceeds record limit")
    if actual != count:
        raise ValueError("resume ZIP directory count mismatch")
    return start


def _resume_member_bounds(raw, member_end, info):
    """Prove that Python and JSZip index the same local member, without inflating it.

    JSZip reads local names, defaults to UTF-8 (zipfile uses CP437), honors
    Unicode-path overrides and turns directory attributes into trailing slashes.
    Accept only names both readers interpret identically. All extra fields here
    come from the already capped central directory; local extras are skipped by
    both readers. ZIP64 overrides are outside the conventional snapshot subset.
    """
    import struct
    import zlib

    if (info.flag_bits & ~0x80e
            or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or (not info.flag_bits & 0x800 and not info.orig_filename.isascii())):
        raise ValueError("unsupported resume ZIP member encoding")
    is_directory = (info.external_attr & 0x10
                    or (info.create_system == 3 and (info.external_attr >> 16) & 0x4000))
    if is_directory and not info.is_dir():
        raise ValueError("resume ZIP directory name mismatch")
    expected_name = info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "ascii")
    cursor = 0
    while cursor < len(info.extra):
        if cursor + 4 > len(info.extra):
            raise ValueError("incomplete resume ZIP extra field")
        kind, size = struct.unpack_from("<HH", info.extra, cursor)
        cursor += 4
        end = cursor + size
        if end > len(info.extra) or kind == 1:
            raise ValueError("unsupported resume ZIP extra field")
        if kind == 0x7075:
            # Do not rely on whether this Python version applies Unicode path
            # fields. JSZip must not obtain an alternate name from any of them.
            data = info.extra[cursor:end]
            if (len(data) < 5 or data[0] != 1
                    or struct.unpack_from("<I", data, 1)[0] != zlib.crc32(expected_name)
                    or data[5:].decode("utf-8") != info.orig_filename):
                raise ValueError("ambiguous resume ZIP Unicode path")
        cursor = end
    offset = info.header_offset
    if offset < 0 or offset + 30 > member_end or raw[offset:offset + 4] != b"PK\x03\x04":
        raise ValueError("invalid resume ZIP local header")
    flags, method = struct.unpack_from("<2H", raw, offset + 6)
    name_size, extra_size = struct.unpack_from("<2H", raw, offset + 26)
    name_start = offset + 30
    start = name_start + name_size + extra_size
    end = start + info.compress_size
    if end > member_end or flags != info.flag_bits or method != info.compress_type:
        raise ValueError("inconsistent resume ZIP member")
    if name_size != len(expected_name) or raw[name_start:name_start + name_size] != expected_name:
        raise ValueError("resume ZIP member name mismatch")
    return start, end


def _resume_member_bytes(raw, bounds, info):
    """Decode stored/deflated XML with a cap on ACTUAL output, then check CRC.

    ZipExtFile truncates output to the declared size and may flush without an
    output limit. Its public read surface cannot prove this bound. Read the
    compressed bytes from our in-memory snapshot and use zlib's max_length;
    never flush or continue after exceeding the cap. Other codecs fall back.
    """
    import zlib

    if info.is_dir() or info.file_size > MAX_RESUME_XML_BYTES:
        raise ValueError("oversized or non-file resume XML member")
    start, end = bounds
    method = info.compress_type
    compressed = memoryview(raw)[start:end]
    if method == zipfile.ZIP_STORED:
        if len(compressed) > MAX_RESUME_XML_BYTES:
            raise ValueError("resume XML exceeds size limit")
        output = bytes(compressed)
    elif method == zipfile.ZIP_DEFLATED:
        inflater = zlib.decompressobj(-15)
        output = inflater.decompress(compressed, MAX_RESUME_XML_BYTES + 1)
        if len(output) > MAX_RESUME_XML_BYTES or not inflater.eof or inflater.unused_data:
            raise ValueError("oversized or incomplete resume deflate stream")
    else:
        raise ValueError("unsupported resume ZIP compression")
    if len(output) != info.file_size or zlib.crc32(output) != info.CRC:
        raise ValueError("resume ZIP size or CRC mismatch")
    return output


def compute_cfi_point(epub_path, source, location_type, value):
    """Resolve a Kobo progress span to a source-document point, or None.

    Uses the same DOM-to-CFI walk as highlights, but requires an unambiguous
    chapter and an actual text node. No basename guessing, context matching,
    highlight-range passthrough, or separate KEPUB substitution is allowed.
    """
    snapshot = _resume_snapshot(epub_path, source, location_type, value)
    return snapshot[0] if snapshot else None


def _resume_snapshot(epub_path, source, location_type, value):
    """Return (point, archive SHA256) from one bounded, stable file snapshot."""
    import hashlib
    import io
    import os
    import posixpath
    import stat
    from urllib.parse import unquote
    from lxml import etree

    if (location_type != "KoboSpan" or not isinstance(source, str)
            or not source or len(source) > 2048 or not isinstance(value, str)
            or len(value) > 128 or not re.fullmatch(r"kobo\.[0-9]+\.[0-9]+", value)):
        return None
    try:
        path = Path(epub_path)
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RESUME_ARCHIVE_BYTES:
                return None
            raw = stream.read(MAX_RESUME_ARCHIVE_BYTES + 1)
            after = os.fstat(stream.fileno())
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if (len(raw) > MAX_RESUME_ARCHIVE_BYTES or identity(before) != identity(after)
                or identity(after) != identity(path.stat())):
            return None
        directory_start = _resume_directory_start(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            # JSZip resolves dot components before indexing members. Refuse
            # aliases across the entire directory before selecting any XML;
            # a matching archive hash cannot prove both readers chose the same
            # chapter. Permit the trailing slash of canonical directory entries.
            names = set()
            for info in archive.infolist():
                name = info.filename.removesuffix("/")
                if (not name or name.startswith("/") or "\\" in name
                        or info.filename != info.orig_filename
                        or any(part in ("", ".", "..") for part in name.split("/"))
                        or name in names):
                    return None
                names.add(name)

            # Check EVERY local header, including unused chapters and images.
            # Adjacent offsets bound each member and reject shared/overlapping
            # local records. Sorting replaces repeated offset scans per XML.
            entries = sorted(archive.infolist(), key=lambda info: info.header_offset)
            bounds = {}
            for index, info in enumerate(entries):
                member_end = entries[index + 1].header_offset if index + 1 < len(entries) else directory_start
                bounds[info.filename] = _resume_member_bounds(raw, member_end, info)

            def xml(name):
                info = archive.getinfo(name)
                member_bytes = _resume_member_bytes(raw, bounds[name], info)
                return etree.fromstring(member_bytes, etree.XMLParser(
                    resolve_entities=False, no_network=True, load_dtd=False))

            container = xml("META-INF/container.xml")
            rootfile = container.find(".//c:rootfile", _CONTAINER_NS)
            if rootfile is None:
                return None
            opf_path = rootfile.get("full-path")
            package = xml(opf_path)
            spine = package.find("opf:spine", _OPF_NS)
            if spine is None:
                return None
            # The shared highlight walk uses /6; packages with a differently
            # placed spine need their actual element step instead.
            spine_step = _cfi_element_step(spine)
            # Decode, normalize AND compare once per distinct href. Caching
            # just the decoded string would still repeat long comparisons for
            # every itemref in a highly compressed, repetitive spine.
            decoded_source = unquote(source)
            opf_dir = posixpath.dirname(opf_path)
            href_matches = {}
            manifest = {}
            for item in package.findall("opf:manifest/opf:item", _OPF_NS):
                href = item.get("href")
                if href and len(href) > MAX_RESUME_HREF_CHARS:
                    return None
                if href not in href_matches:
                    member = None
                    if href and "#" not in href:
                        decoded_href = unquote(href)
                        candidate = posixpath.normpath(posixpath.join(opf_dir, decoded_href))
                        if decoded_source in (candidate, decoded_href):
                            member = candidate
                    href_matches[href] = member
                manifest[item.get("id")] = href_matches[href]
            matches = []
            for index, ref in enumerate(spine.findall("opf:itemref", _OPF_NS)):
                member = manifest.get(ref.get("idref"))
                if member is not None:
                    matches.append((index, member))
                    if len(matches) > 1:
                        return None
            if len(matches) != 1:
                return None
            index, member = matches[0]
            tree = xml(member)
            spans = tree.xpath("//*[@id=$v]", v=value)
            if len(spans) != 1 or not spans[0].text:
                return None
            span = spans[0]
            # The shared walk anchors the body as /4. Validate that convention
            # and avoid assertion delimiters the highlight walk does not escape.
            body = next((e for e in span.iterancestors() if etree.QName(e).localname == "body"), None)
            if body is None or _cfi_element_step(body).split("[")[0] != "/4":
                return None
            for element in [spine, body, span, *span.iterancestors()]:
                element_id = element.get("id")
                if element_id and not _RESUME_ASSERTION_ID.fullmatch(element_id):
                    return None
            cfi_range = _kepub_range_cfi(tree, f"{spine_step}/{2 * (index + 1)}",
                                        value, value, 0, 0)
            if not cfi_range:
                return None
            common, start, _end = cfi_range[8:-1].split(",")
            return f"epubcfi({common}{start})", hashlib.sha256(raw).hexdigest()
    except Exception:
        log.debug("Could not resolve Kobo reading point", exc_info=True)
        return None


@lru_cache(maxsize=64)
def _get_spine(cache_key: tuple, epub_path_arg: Path) -> list[str]:
    """Cache-keyed wrapper over :func:`parse_spine`. ``cache_key``
    encodes ``(path_str, mtime_ns)`` so a re-uploaded EPUB invalidates
    automatically. ``epub_path_arg`` is the actual ``Path`` used —
    passing it explicitly lets lru_cache invalidate on stat changes
    without re-stat'ing on hits."""
    return parse_spine(epub_path_arg)


def parse_spine(epub_path: Path) -> list[str]:
    """Return the EPUB's spine — a list of chapter HTML hrefs in
    reading order. Used to compute the ``/6/2N`` part of the CFI."""
    if not isinstance(epub_path, Path):
        epub_path = Path(epub_path)

    with zipfile.ZipFile(epub_path) as zf:
        try:
            container = zf.read("META-INF/container.xml").decode("utf-8")
        except KeyError:
            opf_path = "content.opf"
        else:
            root = ET.fromstring(container)
            rf = root.find(".//c:rootfile", _CONTAINER_NS)
            if rf is not None and rf.get("full-path"):
                opf_path = rf.get("full-path")
            else:
                opf_path = "content.opf"

        try:
            opf = zf.read(opf_path).decode("utf-8")
        except KeyError:
            return []

    root = ET.fromstring(opf)
    manifest = {
        it.attrib["id"]: it.attrib.get("href", "")
        for it in root.findall(".//opf:manifest/opf:item", _OPF_NS)
    }
    spine_refs = root.findall(".//opf:spine/opf:itemref", _OPF_NS)
    out = []
    for ref in spine_refs:
        idref = ref.get("idref")
        if idref and idref in manifest:
            out.append(manifest[idref])
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_kobospan_id(container_path: str) -> Optional[str]:
    """Parse ``span#kobo\\.4\\.1`` → ``kobo.4.1``. Returns ``None`` if
    no ``#<id>`` fragment present (e.g. a plain-EPUB DOM path)."""
    if not container_path:
        return None
    m = _KOBOSPAN_PATH_RE.search(container_path)
    if not m:
        return None
    return m.group(1).replace("\\", "")


def _cfi_element_step(el) -> str:
    """One CFI step for an element relative to its parent, using
    epub.js's numbering: element children are numbered 2, 4, 6, … (the
    Nth element child, 1-based, times two), with the element's ``id``
    appended as an assertion. Text nodes don't shift element numbering —
    lxml only yields element/comment/PI children when iterating, so the
    index is naturally element-only, matching epub.js's ``.children``."""
    parent = el.getparent()
    if parent is None:
        return ""
    sibs = [c for c in parent if isinstance(c.tag, str)]
    try:
        idx = sibs.index(el)
    except ValueError:
        idx = 0
    step = f"/{2 * (idx + 1)}"
    eid = el.get("id")
    return step + (f"[{eid}]" if eid else "")


def _element_chain_below_body(el):
    """Return the element chain ``[body_child, …, el]`` — every element
    from ``<body>``'s direct child down to ``el`` inclusive, excluding
    ``<body>`` itself (the CFI anchors body as the literal ``/4`` prefix,
    matching epub.js and the EPUB CFI convention for ``<html><head/>
    <body/></html>``)."""
    chain = []
    cur = el
    while cur is not None:
        parent = cur.getparent()
        if parent is None:
            break
        chain.append(cur)
        ptag = parent.tag if isinstance(parent.tag, str) else ""
        if ptag == "body" or ptag.endswith("}body"):
            break
        cur = parent
    chain.reverse()
    return chain


def _kepub_range_cfi(tree, spine_step, start_id, end_id, start_offset, end_offset):
    """Build a valid 3-part EPUB CFI range
    (``epubcfi(<common>,<start>,<end>)``) anchoring a highlight between
    two KoboSpans in the parsed chapter ``tree``.

    The CFI is computed against the *source* document (no reader
    wrappers), so it is portable — a spec-compliant resolver follows the
    element path and validates the ``[kobo.x.y]`` id assertions. The web
    reader does NOT consume this string: epub.js injects wrapper divs at
    render time that shift every step, so the reader regenerates its own
    wrapper-aware CFI client-side from the KoboSpan id (see
    ``cps/static/js/reading/annotations.js``). Returns ``None`` if either
    span is absent from the chapter."""
    start_matches = tree.xpath("//*[@id=$v]", v=start_id)
    end_matches = tree.xpath("//*[@id=$v]", v=end_id)
    if not start_matches or not end_matches:
        return None
    start_el, end_el = start_matches[0], end_matches[0]

    start_chain = _element_chain_below_body(start_el)
    end_chain = _element_chain_below_body(end_el)
    if not start_chain or not end_chain:
        return None

    # Deepest shared ancestor element (compare by identity).
    common_len = 0
    for a, b in zip(start_chain, end_chain):
        if a is b:
            common_len += 1
        else:
            break
    common_chain = start_chain[:common_len]
    start_rest = start_chain[common_len:]
    end_rest = end_chain[common_len:]

    base = f"{spine_step}!/4" + "".join(_cfi_element_step(e) for e in common_chain)
    if not start_rest and not end_rest:
        # Same KoboSpan — the common path already reaches it. Both ends
        # are text-node offsets into that span's first text node (/1).
        return f"epubcfi({base},/1:{start_offset},/1:{end_offset})"
    start_path = "".join(_cfi_element_step(e) for e in start_rest) + f"/1:{start_offset}"
    end_path = "".join(_cfi_element_step(e) for e in end_rest) + f"/1:{end_offset}"
    return f"epubcfi({base},{start_path},{end_path})"


def _child_index_to_cfi_step(child_index: Optional[int]) -> Optional[str]:
    """Convert ``StartContainerChildIndex`` to a CFI step. Kobo's
    1-indexed Nth-child becomes CFI's ``/2N`` even-step convention.
    Returns ``None`` if the index is missing or the sentinel value."""
    if child_index is None or child_index == KOBO_SELECTOR_SENTINEL or child_index <= 0:
        return None
    return f"/{2 * child_index}"


def _resolve_spine_index(spine: list[str], chapter_file: str) -> Optional[int]:
    """Match the ``chapter_file`` (a bare basename) against entries in
    ``spine`` (which may have ``OEBPS/`` or other prefixes)."""
    for i, href in enumerate(spine):
        if href == chapter_file or href.endswith("/" + chapter_file):
            return i
    return None


def _fallback_via_context(
    epub_path: Path,
    cache_key: tuple,
    chapter_file: str,
    spine_step: str,
    position: KoboPosition,
) -> Optional[str]:
    """Last-resort: parse the chapter DOM and search for
    ``context_string`` to derive an approximate text-offset CFI. The
    resulting CFI is intentionally less precise than the KoboSpan
    fast-path — it points at the chapter root with a text-content
    offset, which epub.js can still render as a highlight, just with
    paragraph-level rather than span-level accuracy."""
    if not position.context_string:
        return None
    try:
        tree = _get_chapter_dom(cache_key, epub_path, chapter_file)
    except Exception as e:
        log.warning(
            "compute_cfi_range fallback: DOM parse failed for %s::%s — %s",
            epub_path, chapter_file, e,
        )
        return None
    if tree is None:
        return None
    body_text = tree.text_content() or ""
    idx = body_text.find(position.context_string)
    if idx < 0:
        # Try a tighter slice — Kobo's ContextString includes ±50 chars
        # of surrounding text; the highlight itself is at offset
        # `start_offset` within that.
        if position.start_offset < len(position.context_string):
            anchor = position.context_string[position.start_offset:]
            idx = body_text.find(anchor[:80] if len(anchor) > 80 else anchor)
        if idx < 0:
            return None
    # CFI step into the body's text content — same `spine_step!/4`
    # rendition fragment, then a single text-offset.
    return f"epubcfi({spine_step}!/4:{idx},/4:{idx + (position.end_offset - position.start_offset)})"


@lru_cache(maxsize=256)
def _get_chapter_dom(cache_key: tuple, epub_path_arg: Path, chapter_file: str):
    """Cache the parsed lxml tree for one chapter. Invalidated by the
    same ``cache_key`` ``(path, mtime_ns)`` as ``_get_spine`` so
    re-uploads bust both caches together."""
    with zipfile.ZipFile(epub_path_arg) as zf:
        candidates = [chapter_file] + [
            n for n in zf.namelist() if n.endswith("/" + chapter_file)
        ]
        for name in candidates:
            try:
                raw = zf.read(name)
            except KeyError:
                continue
            try:
                return lxml_html.fromstring(raw)
            except Exception:
                continue
    return None
