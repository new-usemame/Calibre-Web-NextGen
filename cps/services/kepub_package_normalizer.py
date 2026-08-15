# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make kepubify output safe for Kobo's non-normalizing package resolver."""

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


def _package_document_path(archive):
    container = etree.fromstring(archive.read(_CONTAINER_PATH), parser=_XML_PARSER)
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


def _split_local_reference(value):
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path.startswith("/"):
        return None
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


def _validate_rewritten_archive(path, expected_span_counts):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise ValueError("mimetype is not the first EPUB entry")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("mimetype is compressed")
        if archive.read(infos[0]) != b"application/epub+zip":
            raise ValueError("mimetype has unexpected content")
        if archive.testzip() is not None:
            raise ValueError("rewritten KEPUB failed its CRC check")
        opf_path = _package_document_path(archive)
        opf_directory = posixpath.dirname(opf_path)
        for item in _manifest_items(archive.read(opf_path)):
            href = item.get("href")
            if not href:
                continue
            split = _split_local_reference(href)
            if split is None:
                continue
            _, href_path = split
            if not _inside_directory(_resolve_reference(opf_path, href_path), opf_directory):
                raise ValueError("rewritten manifest still escapes the OPF directory")
        contents = {info.filename: archive.read(info) for info in infos}
        if _span_counts(contents) != expected_span_counts:
            raise ValueError("KoboSpan counts changed while normalizing the package")


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
            names = [info.filename for info in infos]
            opf_path = _package_document_path(archive)
            opf_bytes = archive.read(opf_path)
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
        _validate_rewritten_archive(temporary_path, expected_span_counts)
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
            names = {info.filename for info in archive.infolist()}
            opf_path = _package_document_path(archive)
            opf_bytes = archive.read(opf_path)
            return bool(_plan_relocations(opf_path, opf_bytes, names))
    except Exception as error:
        log.warning("Could not inspect KEPUB package %s: %s", path, error)
        return None
