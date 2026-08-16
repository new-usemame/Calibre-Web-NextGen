# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make kepubify output safe for Kobo's non-normalizing package resolver."""

from collections import Counter
import copy
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from lxml import etree

from .. import logger


log = logger.create()

_CONTAINER_PATH = "META-INF/container.xml"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_TEXT_DOCUMENT_SUFFIXES = (
    ".css", ".htm", ".html", ".ncx", ".opf", ".svg", ".xhtml", ".xml",
)
_REFERENCE_ATTRIBUTE_RE = re.compile(
    rb"(?P<prefix>\b(?:[A-Za-z_][\w.-]*:)?(?:href|src)\s*=\s*)"
    rb"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_CSS_URL_RE = re.compile(
    rb"(?P<prefix>\burl\(\s*)(?P<quote>['\"]?)(?P<value>.*?)"
    rb"(?P=quote)(?P<suffix>\s*\))",
    re.IGNORECASE | re.DOTALL,
)
_KOBO_SPAN_RE = re.compile(
    rb"<(?:(?:[A-Za-z_][\w.-]*):)?span\b[^>]*\bclass\s*=\s*"
    rb"(['\"])[^'\"]*\bkoboSpan\b[^'\"]*\1[^>]*>",
    re.IGNORECASE,
)
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)


#: This implementation temporarily holds the source members, rewritten members,
#: output ZIP and validation members at the same time. A 2 GiB input therefore
#: implied several GiB of peak memory -- not a meaningful worker-safety bound.
#: 256 MiB still accommodates unusually image-heavy books while capping that
#: multi-copy peak near a scale a normal container can survive. On refusal the
#: original is untouched and conversion retains its existing delivery fallback.
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

#: Structural XML is read before relocation can be planned, so it also needs a
#: per-entry bound. Real container.xml files are normally under 1 KiB; 1 MiB is
#: generous. A very large manifest can be legitimate, hence a separate 16 MiB
#: ceiling, but it must not be able to allocate hundreds of MiB on its own.
MAX_CONTAINER_XML_BYTES = 1 * 1024 * 1024
MAX_PACKAGE_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_TOC_DOCUMENT_BYTES = 16 * 1024 * 1024


def _reject_oversized_archive(infos):
    total = 0
    for info in infos:
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError(
                "KEPUB decompresses to more than %d bytes; refusing to load it"
                % MAX_TOTAL_UNCOMPRESSED_BYTES
            )


def _read_bounded_member(archive, name, limit, description):
    """Read one structural member without trusting only ZIP metadata."""
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ValueError("KEPUB is missing {}".format(description)) from error
    if info.file_size > limit:
        raise ValueError("{} exceeds {} bytes".format(description, limit))
    with archive.open(info, "r") as member:
        content = member.read(limit + 1)
    if len(content) > limit:
        raise ValueError("{} exceeds {} bytes".format(description, limit))
    return content


def _package_document_path(archive):
    container = etree.fromstring(
        _read_bounded_member(
            archive, _CONTAINER_PATH, MAX_CONTAINER_XML_BYTES, "container.xml"),
        parser=_XML_PARSER,
    )
    rootfiles = container.xpath(
        "//container:rootfile/@full-path", namespaces={"container": _CONTAINER_NS})
    if not rootfiles:
        raise ValueError("EPUB container does not name a package document")
    package_path = posixpath.normpath(rootfiles[0])
    if package_path.startswith("../") or package_path.startswith("/"):
        raise ValueError("EPUB package document path escapes the archive")
    return package_path


def _manifest_items(opf_bytes):
    package = etree.fromstring(opf_bytes, parser=_XML_PARSER)
    return package.xpath("//*[local-name()='manifest']/*[local-name()='item']")


def _toc_documents(opf_path, opf_bytes):
    for item in _manifest_items(opf_bytes):
        media_type = item.get("media-type", "")
        properties = set(item.get("properties", "").split())
        if media_type == "application/x-dtbncx+xml":
            kind = "NCX"
        elif "nav" in properties:
            kind = "navigation"
        else:
            continue
        href = item.get("href")
        if not href:
            continue
        split = _split_local_reference(href)
        if split is None:
            continue
        _, href_path = split
        toc_path = _resolve_reference(opf_path, href_path)
        if toc_path.startswith("../") or toc_path.startswith("/"):
            raise ValueError("EPUB TOC path escapes the archive")
        yield toc_path, kind


def _toc_targets(toc_bytes, kind):
    document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
    if kind == "NCX":
        return document.xpath("//*[local-name()='content']/@src")

    targets = []
    for nav in document.xpath("//*[local-name()='nav']"):
        epub_types = set()
        for value in nav.xpath("@*[local-name()='type']"):
            epub_types.update(value.split())
        roles = set(nav.get("role", "").split())
        if "toc" in epub_types or "doc-toc" in roles:
            targets.extend(nav.xpath(".//*[local-name()='a']/@href"))
    return targets


def count_fragment_anchored_toc_targets(path):
    """Count distinct fragment-bearing targets in manifest-declared TOCs.

    Return zero when the archive or package document cannot be inspected.
    Malformed individual TOCs are logged and skipped. This diagnostic never
    modifies the EPUB and never lets user-file failures escape into conversion.
    """
    path = os.fspath(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _reject_oversized_archive(infos)
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            toc_documents = list(_toc_documents(opf_path, opf_bytes))
            fragment_targets = set()
            for toc_path, kind in toc_documents:
                try:
                    toc_bytes = _read_bounded_member(
                        archive, toc_path, MAX_TOC_DOCUMENT_BYTES,
                        "{} TOC document".format(kind))
                    for target in _toc_targets(toc_bytes, kind):
                        try:
                            split = _split_local_reference(target)
                        except (TypeError, ValueError):
                            continue
                        if split is not None and split[0].fragment:
                            _, target_path = split
                            resolved_path = _resolve_reference(toc_path, target_path)
                            fragment_targets.add((resolved_path, split[0].fragment))
                except Exception as error:
                    log.warning("Could not inspect %s TOC document in %s: %s",
                                kind, path, error)
            return len(fragment_targets)
    except Exception as error:
        log.warning("Could not inspect EPUB TOC fragments in %s: %s", path, error)
        return 0


def _split_local_reference(value):
    """Split a local reference into (urlsplit parts, decoded path), or None when
    it points outside the package entirely.

    ``None`` means "not ours to touch" and every caller treats it as skip, so it
    must mean ONLY that. An absolute local path is not external -- it is a
    reference we cannot contain -- and silently skipping it would let a manifest
    href escape the OPF directory while we report the package clean, which is the
    exact invariant this module exists to guarantee.
    """
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None  # genuinely external (http:, data:, //host/...) -- leave alone
    if parts.path.startswith("/"):
        raise ValueError(
            "EPUB reference is an absolute path and cannot be contained: %r" % value
        )
    path = unquote(parts.path)
    if "\\" in path:
        raise ValueError("EPUB reference contains a backslash")
    return parts, path


def _resolve_reference(document_path, reference_path):
    return posixpath.normpath(posixpath.join(posixpath.dirname(document_path), reference_path))


def _inside_directory(path, directory):
    if not directory or directory == ".":
        return not path.startswith("../") and path != ".." and not path.startswith("/")
    return path == directory or path.startswith(directory + "/")


def _collision_safe_destination(source, opf_directory, occupied):
    basename = posixpath.basename(source)
    stem, extension = posixpath.splitext(basename)
    candidate = posixpath.join(opf_directory, basename) if opf_directory else basename
    counter = 1
    while candidate in occupied:
        renamed = "{}-{}{}".format(stem, counter, extension)
        candidate = posixpath.join(opf_directory, renamed) if opf_directory else renamed
        counter += 1
    return candidate


def _plan_relocations(opf_path, opf_bytes, archive_names):
    opf_directory = posixpath.dirname(opf_path)
    occupied = set(archive_names)
    relocations = {}
    for item in _manifest_items(opf_bytes):
        href = item.get("href")
        if not href:
            continue
        split = _split_local_reference(href)
        if split is None:
            continue
        _, href_path = split
        source = _resolve_reference(opf_path, href_path)
        if _inside_directory(source, opf_directory):
            continue
        if source not in archive_names:
            raise ValueError("escaping manifest item is missing: {}".format(source))
        if source not in relocations:
            destination = _collision_safe_destination(source, opf_directory, occupied)
            relocations[source] = destination
            occupied.add(destination)
    return relocations


def _encoded_relative_path(target, document_path):
    base = posixpath.dirname(document_path) or "."
    relative = posixpath.relpath(target, base)
    return quote(relative, safe="/@:+!$&'()*+,;=-._~")


def _rewrite_reference(value, old_document, new_document, relocations, rewrite_all):
    try:
        split = _split_local_reference(value)
    except (TypeError, ValueError):
        return value
    if split is None:
        return value
    parts, reference_path = split
    if not reference_path:
        return value
    original_target = _resolve_reference(old_document, reference_path)
    target = relocations.get(original_target, original_target)
    if not rewrite_all and target == original_target:
        return value
    new_path = _encoded_relative_path(target, new_document)
    return urlunsplit(("", "", new_path, parts.query, parts.fragment))


def _rewrite_document(content, old_document, new_document, relocations):
    rewrite_all = old_document != new_document

    def replace_attribute(match):
        value = match.group("value").decode("utf-8")
        rewritten = _rewrite_reference(
            value, old_document, new_document, relocations, rewrite_all)
        if rewritten == value:
            return match.group(0)
        return (match.group("prefix") + match.group("quote")
                + rewritten.encode("utf-8") + match.group("quote"))

    def replace_css_url(match):
        value = match.group("value").decode("utf-8")
        rewritten = _rewrite_reference(
            value, old_document, new_document, relocations, rewrite_all)
        if rewritten == value:
            return match.group(0)
        return (match.group("prefix") + match.group("quote")
                + rewritten.encode("utf-8") + match.group("quote") + match.group("suffix"))

    rewritten = _REFERENCE_ATTRIBUTE_RE.sub(replace_attribute, content)
    if old_document.lower().endswith(".css"):
        rewritten = _CSS_URL_RE.sub(replace_css_url, rewritten)
    return rewritten


def _rewritten_entries(infos, contents, relocations):
    entries = []
    for info in infos:
        old_name = info.filename
        new_name = relocations.get(old_name, old_name)
        content = contents[old_name]
        if (old_name in relocations
                or old_name.lower().endswith(_TEXT_DOCUMENT_SUFFIXES)):
            content = _rewrite_document(content, old_name, new_name, relocations)
        new_info = copy.copy(info)
        new_info.filename = new_name
        new_info.orig_filename = new_name
        entries.append((new_info, content))
    return entries


def _span_counts(contents, relocations=None):
    relocations = relocations or {}
    return {
        relocations.get(name, name): len(_KOBO_SPAN_RE.findall(content))
        for name, content in contents.items()
    }


def _write_archive(path, entries, comment):
    mimetype = next((entry for entry in entries if entry[0].filename == "mimetype"), None)
    if mimetype is None:
        raise ValueError("EPUB archive has no mimetype entry")
    ordered = [mimetype] + [entry for entry in entries if entry is not mimetype]
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.comment = comment
        for info, content in ordered:
            if info.filename == "mimetype":
                info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, content)


def _escaping_manifest_references(opf_path, opf_bytes):
    """Count escaping ``manifest/item@href`` references by item identity.

    An item's ``id`` is its stable identity. An item without an ``id`` falls
    back to its ordinal position among the package's manifest items, so adding,
    moving, or reassigning an unidentified escaping item fails closed.
    """
    opf_directory = posixpath.dirname(opf_path)
    escaping = Counter()
    for position, item in enumerate(_manifest_items(opf_bytes)):
        href = item.get("href")
        if not href:
            continue
        split = _split_local_reference(href)
        if split is None:
            continue
        _, href_path = split
        if not _inside_directory(
                _resolve_reference(opf_path, href_path), opf_directory):
            item_id = item.get("id")
            identity = ("id", item_id) if item_id else ("position", position)
            escaping[(identity, href)] += 1
    return escaping


def _validate_rewritten_archive(path, expected_span_counts):
    """Validate archive-integrity invariants shared by every rewrite."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        _reject_oversized_archive(infos)
        if not infos or infos[0].filename != "mimetype":
            raise ValueError("mimetype is not the first EPUB entry")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("mimetype is compressed")
        if archive.read(infos[0]) != b"application/epub+zip":
            raise ValueError("mimetype has unexpected content")
        if archive.testzip() is not None:
            raise ValueError("rewritten KEPUB failed its CRC check")
        opf_path = _package_document_path(archive)
        opf_bytes = _read_bounded_member(
            archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
        contents = {info.filename: archive.read(info) for info in infos}
        if _span_counts(contents) != expected_span_counts:
            raise ValueError("KoboSpan counts changed during package rewrite")
        return opf_path, opf_bytes


def _validate_package_document_rewrite(
        path, expected_span_counts, source_escaping_references):
    opf_path, opf_bytes = _validate_rewritten_archive(
        path, expected_span_counts)
    rewritten_escaping_references = _escaping_manifest_references(
        opf_path, opf_bytes)
    if rewritten_escaping_references - source_escaping_references:
        raise ValueError("package rewrite introduced an escaping manifest href")


def _validate_normalized_archive(path, expected_span_counts):
    opf_path, opf_bytes = _validate_rewritten_archive(
        path, expected_span_counts)
    if _escaping_manifest_references(opf_path, opf_bytes):
        raise ValueError("rewritten manifest still escapes the OPF directory")


def rewrite_package_document(path, transform):
    """Transform a KEPUB package document with an atomic, validated rewrite.

    ``transform`` receives the parsed package element and must return truthy only
    when it mutated that element. Return ``True`` when the archive was replaced,
    ``False`` for a byte-identical no-op, and ``None`` on failure. On failure the
    source archive is untouched and the temporary archive is removed.

    The transaction preserves every non-package member's bytes, the archive
    comment, and the source file's permission bits. Existing escaping manifest
    ``item`` hrefs may remain only with the same raw spelling, item identity,
    and multiplicity; an item without an ``id`` is identified by its manifest
    position. This check covers only manifest ``item`` hrefs, not ``guide``
    references, metadata ``link`` elements, or ``xml:base``.
    """
    path = os.fspath(path)
    temporary_path = None
    try:
        original_stat = os.stat(path)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _reject_oversized_archive(infos)
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("KEPUB contains duplicate ZIP member names")
            if archive.testzip() is not None:
                raise ValueError("KEPUB failed its CRC check")

            package_path = _package_document_path(archive)
            package_bytes = _read_bounded_member(
                archive,
                package_path,
                MAX_PACKAGE_DOCUMENT_BYTES,
                "package document",
            )
            source_escaping_references = _escaping_manifest_references(
                package_path, package_bytes)
            package = etree.fromstring(package_bytes, parser=_XML_PARSER)
            if not transform(package):
                return False

            contents = {info.filename: archive.read(info) for info in infos}
            expected_span_counts = _span_counts(contents)
            contents[package_path] = etree.tostring(
                package.getroottree(),
                encoding="utf-8",
                xml_declaration=package_bytes.lstrip().startswith(b"<?xml"),
            )
            entries = [(info, contents[info.filename]) for info in infos]
            comment = archive.comment

        descriptor, temporary_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)),
            prefix="." + os.path.basename(path) + ".",
            suffix=".package-rewrite.tmp",
        )
        os.close(descriptor)
        _write_archive(temporary_path, entries, comment)
        _validate_package_document_rewrite(
            temporary_path, expected_span_counts, source_escaping_references)

        with zipfile.ZipFile(temporary_path) as rewritten:
            if rewritten.comment != comment:
                raise ValueError("archive comment changed during package rewrite")
            for name, content in contents.items():
                if name != package_path and rewritten.read(name) != content:
                    raise ValueError(
                        "non-package ZIP member changed during package rewrite: " + name
                    )

        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        os.replace(temporary_path, path)
        temporary_path = None
        return True
    except Exception as error:
        log.warning(
            "Could not rewrite KEPUB package document %s; original preserved: %s",
            path,
            error,
        )
        return None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def normalize_kepub_package(path):
    """Normalize escaping OPF manifest items in ``path`` atomically.

    Return ``True`` when the archive changed, ``False`` for a clean byte-identical
    no-op, and ``None`` when validation failed. Failures are logged and never
    escape; the original archive is left untouched.
    """
    path = os.fspath(path)
    temporary_path = None
    try:
        original_stat = os.stat(path)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            # This is intentionally the first operation after reading the central
            # directory: no member read and no full CRC/decompression pass may
            # happen before the cheap declared-size rejection.
            _reject_oversized_archive(infos)
            names = [info.filename for info in infos]
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            relocations = _plan_relocations(opf_path, opf_bytes, set(names))
            if not relocations:
                return False
            if len(names) != len(set(names)):
                raise ValueError("KEPUB contains duplicate ZIP member names")
            if archive.testzip() is not None:
                raise ValueError("KEPUB failed its CRC check")
            contents = {info.filename: archive.read(info) for info in infos}
            entries = _rewritten_entries(infos, contents, relocations)
            expected_span_counts = _span_counts(contents, relocations)
            comment = archive.comment

        descriptor, temporary_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)),
            prefix="." + os.path.basename(path) + ".",
            suffix=".normalize.tmp",
        )
        os.close(descriptor)
        _write_archive(temporary_path, entries, comment)
        _validate_normalized_archive(temporary_path, expected_span_counts)
        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        os.replace(temporary_path, path)
        temporary_path = None
        return True
    except Exception as error:
        log.warning("Could not normalize KEPUB package %s; original preserved: %s", path, error)
        return None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def kepub_package_needs_normalization(path):
    """Cheap read-only probe: inspect only container.xml and the package document.

    Return ``True`` for an escaping manifest item, ``False`` for a clean package,
    and ``None`` when the package cannot be inspected. The archive is never
    materialized, preserving the clean-library fast path introduced in #1639.
    """
    path = os.fspath(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _reject_oversized_archive(infos)
            names = {info.filename for info in infos}
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            return bool(_plan_relocations(opf_path, opf_bytes, names))
    except Exception as error:
        log.warning("Could not inspect KEPUB package %s: %s", path, error)
        return None
