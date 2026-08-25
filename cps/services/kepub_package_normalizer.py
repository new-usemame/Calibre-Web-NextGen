# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make kepubify output safe for Kobo's non-normalizing package resolver."""

from collections import Counter
import copy
from dataclasses import dataclass
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


class UnsupportedKepubPackage(ValueError):
    """An explicit package-content or declared-size refusal by the normalizer."""


PROBE_CLEAN = "clean"
PROBE_NEEDS_NORMALIZATION = "needs_normalization"
PROBE_UNSUPPORTED = "unsupported"
PROBE_RETRYABLE = "retryable"


@dataclass(frozen=True)
class KepubPackageInspection:
    """Typed repair-probe outcome; failures are never inferred from log text."""

    status: str
    error_message: str | None = None


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
_XML_ATTRIBUTE_RE = re.compile(
    rb"(?P<name>(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*)\s*=\s*"
    rb"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)


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
MAX_CONTENT_DOCUMENT_BYTES = 16 * 1024 * 1024

_RENDERED_PREDECESSOR_ELEMENTS = frozenset({
    "audio", "br", "button", "canvas", "embed", "hr", "iframe", "img", "input",
    "object", "picture", "select", "svg", "table", "video",
})


def _reject_oversized_archive(infos):
    total = 0
    for info in infos:
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise UnsupportedKepubPackage(
                "KEPUB decompresses to more than %d bytes; refusing to load it"
                % MAX_TOTAL_UNCOMPRESSED_BYTES
            )


def _read_bounded_member(archive, name, limit, description):
    """Read one structural member without trusting only ZIP metadata."""
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise UnsupportedKepubPackage(
            "KEPUB is missing {}".format(description)) from error
    if info.file_size > limit:
        raise UnsupportedKepubPackage(
            "{} exceeds {} bytes".format(description, limit))
    with archive.open(info, "r") as member:
        content = member.read(limit + 1)
    if len(content) > limit:
        raise UnsupportedKepubPackage(
            "{} exceeds {} bytes".format(description, limit))
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
        raise UnsupportedKepubPackage(
            "EPUB container does not name a package document")
    package_path = posixpath.normpath(rootfiles[0])
    if package_path.startswith("../") or package_path.startswith("/"):
        raise UnsupportedKepubPackage(
            "EPUB package document path escapes the archive")
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
            raise UnsupportedKepubPackage("EPUB TOC path escapes the archive")
        yield toc_path, kind


def _toc_target_elements(document, kind):
    if kind == "NCX":
        return [
            (element, "src")
            for nav_map in document.xpath("//*[local-name()='navMap']")
            for element in nav_map.xpath(
                ".//*[local-name()='navPoint']/*[local-name()='content'][@src]")
        ]

    targets = []
    for nav in document.xpath("//*[local-name()='nav']"):
        epub_types = set()
        for value in nav.xpath("@*[local-name()='type']"):
            epub_types.update(value.split())
        roles = set(nav.get("role", "").split())
        if "toc" in epub_types or "doc-toc" in roles:
            targets.extend(
                (element, "href")
                for element in nav.xpath(".//*[local-name()='a'][@href]")
            )
    return targets


def _toc_targets(toc_bytes, kind):
    document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
    return [element.get(attribute)
            for element, attribute in _toc_target_elements(document, kind)]


def _contained_toc_target(toc_path, value):
    split = _split_local_reference(value)
    if split is None:
        return None
    parts, target_path = split
    resolved_path = _resolve_reference(toc_path, target_path)
    if resolved_path == ".." or resolved_path.startswith("../") or resolved_path.startswith("/"):
        raise UnsupportedKepubPackage("EPUB TOC target escapes the archive")
    return parts, resolved_path


def _element_local_name(element):
    if not isinstance(element.tag, str):
        return ""
    return etree.QName(element).localname.lower()


def _anchor_is_first_rendered_position(document_bytes, fragment):
    """Whether ``fragment`` starts before any rendered body content.

    The traversal stops at the matching element's start tag. Consequently an
    anchor's own content and the tails of its ancestors are correctly excluded:
    all of those render at or after the target position, never before it.
    """
    try:
        document = etree.fromstring(document_bytes, parser=_XML_PARSER)
    except etree.XMLSyntaxError:
        # Recovery can discard or reorder malformed markup and thereby turn a
        # genuinely mid-document anchor into an apparent first-rendered anchor.
        # An unparseable target therefore fails only this conservative proof.
        return False
    bodies = document.xpath("//*[local-name()='body']")
    if not bodies:
        return False
    body = bodies[0]

    def visit(element):
        if element.get("id") == fragment or element.get("name") == fragment:
            return 1  # found before any disqualifying predecessor
        if (element is not body
                and _element_local_name(element) in _RENDERED_PREDECESSOR_ELEMENTS):
            return -1
        if element.text and element.text.strip():
            return -1
        for child in element:
            # Comments and processing instructions have text in lxml's tree,
            # but that text is markup metadata rather than rendered content.
            if isinstance(child.tag, str):
                result = visit(child)
                if result:
                    return result
            if child.tail and child.tail.strip():
                return -1
        return 0

    return visit(body) == 1


def _xml_start_tag_ranges(source):
    """Yield exact byte ranges for element start tags in well-formed XML."""
    position = 0
    length = len(source)
    while position < length:
        start = source.find(b"<", position)
        if start < 0:
            return
        if source.startswith(b"<!--", start):
            end = source.find(b"-->", start + 4)
            position = length if end < 0 else end + 3
            continue
        if source.startswith(b"<![CDATA[", start):
            end = source.find(b"]]>", start + 9)
            position = length if end < 0 else end + 3
            continue
        if source.startswith(b"<?", start):
            end = source.find(b"?>", start + 2)
            position = length if end < 0 else end + 2
            continue

        closing = source.startswith(b"</", start)
        declaration = source.startswith(b"<!", start)
        quote_byte = None
        bracket_depth = 0
        cursor = start + 2 if closing or declaration else start + 1
        while cursor < length:
            if quote_byte is not None:
                if source[cursor] == quote_byte:
                    quote_byte = None
                cursor += 1
                continue
            # Comments and processing instructions inside a DOCTYPE internal
            # subset may contain bracket characters and text shaped like start
            # tags. Skip them as lexical units so they cannot change the subset
            # depth or this scanner's element numbering relative to lxml's. A
            # desync makes an edit land on the wrong element, which validation
            # then rejects on every repair attempt instead of converging.
            if declaration and source.startswith(b"<!--", cursor):
                comment_end = source.find(b"-->", cursor + 4)
                cursor = length if comment_end < 0 else comment_end + 3
                continue
            if declaration and source.startswith(b"<?", cursor):
                instruction_end = source.find(b"?>", cursor + 2)
                cursor = length if instruction_end < 0 else instruction_end + 2
                continue
            byte = source[cursor]
            if byte in (ord("'"), ord('"')):
                quote_byte = byte
            elif declaration and byte == ord("["):
                bracket_depth += 1
            elif declaration and byte == ord("]") and bracket_depth:
                bracket_depth -= 1
            elif byte == ord(">") and bracket_depth == 0:
                break
            cursor += 1
        if cursor >= length:
            return
        if not closing and not declaration:
            yield start, cursor + 1
        position = cursor + 1


def _rewrite_toc_attributes(source, edits):
    """Apply selected fragment removals without serializing the TOC document.

    ``edits`` contains ``(element_index, local_attribute_name)`` pairs. If the
    exact lexical attribute cannot be identified, that edit is conservatively
    skipped and every source byte remains intact.
    """
    replacements = []
    wanted = set(edits)
    for element_index, (start, end) in enumerate(_xml_start_tag_ranges(source)):
        if not wanted:
            break
        tag = source[start:end]
        for match in _XML_ATTRIBUTE_RE.finditer(tag):
            attribute = match.group("name").rsplit(b":", 1)[-1].decode(
                "ascii", errors="ignore").lower()
            key = (element_index, attribute)
            if key not in wanted:
                continue
            raw_value = match.group("value")
            fragment_at = raw_value.find(b"#")
            if fragment_at < 0:
                continue
            value_start = start + match.start("value")
            replacements.append(
                (value_start, value_start + len(raw_value), raw_value[:fragment_at]))
            wanted.remove(key)

    rewritten = source
    for start, end, replacement in reversed(replacements):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return rewritten


def _plan_toc_fragment_rewrites(archive, opf_path, opf_bytes):
    """Return TOC-member replacements for provably redundant fragments."""
    parsed_tocs = {}
    fragment_targets = []
    malformed_toc = False
    for toc_path, kind in _toc_documents(opf_path, opf_bytes):
        key = (toc_path, kind)
        if key in parsed_tocs:
            continue
        try:
            toc_bytes = _read_bounded_member(
                archive, toc_path, MAX_TOC_DOCUMENT_BYTES,
                "{} TOC document".format(kind))
            document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
        except (etree.XMLSyntaxError, UnsupportedKepubPackage) as error:
            # Uniform fragment-proof rule: anything that prevents proving
            # redundancy skips the affected target/TOC. Only package structure
            # independent of this optional transform may refuse the package.
            log.warning("Could not inspect %s TOC document %s: %s",
                        kind, toc_path, error)
            malformed_toc = True
            continue
        parsed_tocs[key] = (document, toc_bytes)
        element_indexes = {
            element: index
            for index, element in enumerate(
                node for node in document.iter() if isinstance(node.tag, str))
        }
        for element, attribute in _toc_target_elements(document, kind):
            try:
                target = _contained_toc_target(toc_path, element.get(attribute))
            except (TypeError, ValueError, UnsupportedKepubPackage):
                continue
            if target is None:
                continue
            parts, resolved_path = target
            if parts.fragment:
                fragment_targets.append(
                    (toc_path, element_indexes[element], attribute, parts, resolved_path,
                     unquote(parts.fragment)))

    if malformed_toc:
        # Distinct-fragment counting is package-wide. If even one declared TOC
        # is unreadable, stripping from another would no longer satisfy the
        # proof, but unrelated relocation work can still proceed.
        return {}, {}

    fragments_by_document = {}
    for _toc_path, _element, _attribute, _parts, resolved_path, fragment in fragment_targets:
        fragments_by_document.setdefault(resolved_path, set()).add(fragment)

    content_cache = {}
    edits_by_toc = {}
    archive_names = set(archive.namelist())
    for toc_path, element_index, attribute, _parts, resolved_path, fragment in fragment_targets:
        if len(fragments_by_document[resolved_path]) != 1:
            continue
        if resolved_path not in archive_names:
            continue
        if resolved_path not in content_cache:
            try:
                content_cache[resolved_path] = _read_bounded_member(
                    archive, resolved_path, MAX_CONTENT_DOCUMENT_BYTES,
                    "TOC target document")
            except UnsupportedKepubPackage:
                content_cache[resolved_path] = None
        if content_cache[resolved_path] is None:
            continue
        if not _anchor_is_first_rendered_position(content_cache[resolved_path], fragment):
            continue
        edits_by_toc.setdefault(toc_path, set()).add((element_index, attribute))

    rewrites = {}
    source_by_toc = {
        toc_path: toc_bytes
        for (toc_path, _kind), (_document, toc_bytes) in parsed_tocs.items()
    }
    for toc_path, edits in edits_by_toc.items():
        source = source_by_toc[toc_path]
        rewritten = _rewrite_toc_attributes(source, edits)
        if rewritten != source:
            rewrites[toc_path] = rewritten
    return rewrites, edits_by_toc


def _toc_target_identities_from_source(
        opf_path, opf_bytes, contents, planned_edits=None, relocations=None):
    """Build TOC identities from source bytes with explicit planned edits.

    This expectation is independent of serialized/planned TOC output bytes: a
    planner that drops or invents a target therefore cannot validate itself.
    """
    planned_edits = planned_edits or {}
    relocations = relocations or {}
    identities = Counter()
    for toc_path, kind in _toc_documents(opf_path, opf_bytes):
        toc_bytes = contents.get(toc_path)
        destination_toc = relocations.get(toc_path, toc_path)
        if toc_bytes is None:
            identities[(destination_toc, kind, "missing")] += 1
            continue
        try:
            document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
        except etree.XMLSyntaxError:
            identities[(destination_toc, kind, "unparseable")] += 1
            continue
        elements = [node for node in document.iter() if isinstance(node.tag, str)]
        element_indexes = {element: index for index, element in enumerate(elements)}
        for element, attribute in _toc_target_elements(document, kind):
            value = element.get(attribute)
            try:
                target = _contained_toc_target(toc_path, value)
            except (TypeError, ValueError, UnsupportedKepubPackage):
                identities[(destination_toc, kind, "invalid", value)] += 1
                continue
            if target is None:
                identities[(destination_toc, kind, "external", value)] += 1
                continue
            parts, resolved_path = target
            fragment = parts.fragment
            if (element_indexes[element], attribute) in planned_edits.get(toc_path, set()):
                fragment = ""
            destination_target = relocations.get(resolved_path, resolved_path)
            identities[(
                destination_toc, kind, destination_target, parts.query,
                unquote(fragment),
            )] += 1
    return identities


def count_fragment_anchored_toc_targets(path):
    """Count distinct fragment-bearing targets in manifest-declared TOCs.

    Return zero when the archive or package document cannot be inspected.
    Malformed individual TOCs are logged and skipped. This diagnostic never
    modifies the EPUB and never lets user-file failures escape into conversion.

    This counts books AT RISK, not annotations actually affected, and that is
    not a shortcut -- it is the only server-side signal that exists. The device
    derives its local ``Bookmark.ContentID`` from the TOC entry verbatim,
    fragment included, and that value never crosses the wire. OBSERVED
    2026-08-17 on a Kobo Clara BW (4.45.23792): a highlight whose device-local
    ContentID ended ``...-h-3.htm.xhtml#pgepubid00038`` was uploaded with

        {"span": {"chapterFilename": "OEBPS/...-h-3.htm.xhtml", ...}}

    -- no fragment. The only ``#`` in the payload are the KoboSpan anchors in
    ``startPath``/``endPath``, which are unrelated. So the stored annotation is
    correct and always was: on this instance 0 of 622 rows carry a fragment.

    Do not go looking for a query that counts orphaned annotations, and do not
    "fix" ingestion to preserve a fragment it never receives. Whether a given
    annotation renders is decidable only on the device, by checking its
    ContentID against a ``ContentType=9`` row in ``KoboReader.sqlite``.
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
        raise UnsupportedKepubPackage(
            "EPUB reference is an absolute path and cannot be contained: %r" % value
        )
    path = unquote(parts.path)
    if "\\" in path:
        raise UnsupportedKepubPackage("EPUB reference contains a backslash")
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
            raise UnsupportedKepubPackage(
                "escaping manifest item is missing: {}".format(source))
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


def _validate_normalized_archive(
        path, expected_span_counts, expected_toc_contents,
        expected_toc_target_identities):
    opf_path, opf_bytes = _validate_rewritten_archive(
        path, expected_span_counts)
    if _escaping_manifest_references(opf_path, opf_bytes):
        raise ValueError("rewritten manifest still escapes the OPF directory")
    with zipfile.ZipFile(path) as archive:
        for toc_path, expected in expected_toc_contents.items():
            if archive.read(toc_path) != expected:
                raise ValueError(
                    "TOC document differs from its exact planned rewrite: " + toc_path)
        contents = {info.filename: archive.read(info) for info in archive.infolist()}
    actual_identities = _toc_target_identities_from_source(
        opf_path, opf_bytes, contents)
    if actual_identities != expected_toc_target_identities:
        raise ValueError("TOC targets differ from the source-plus-edits plan")


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


def _normalize_kepub_package_only(path):
    """Normalize unsafe package paths and redundant TOC fragments atomically.

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
            if len(names) != len(set(names)):
                raise ValueError("KEPUB contains duplicate ZIP member names")
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            declared_toc_paths = {path for path, _kind in _toc_documents(opf_path, opf_bytes)}
            relocations = _plan_relocations(opf_path, opf_bytes, set(names))
            toc_rewrites, planned_toc_edits = _plan_toc_fragment_rewrites(
                archive, opf_path, opf_bytes)
            if not relocations and not toc_rewrites:
                return False
            if archive.testzip() is not None:
                raise ValueError("KEPUB failed its CRC check")
            source_contents = {info.filename: archive.read(info) for info in infos}
            contents = dict(source_contents)
            contents.update(toc_rewrites)
            entries = _rewritten_entries(infos, contents, relocations)
            expected_span_counts = _span_counts(source_contents, relocations)
            expected_contents = {info.filename: content for info, content in entries}
            # Stronger than a semantic round-trip: each existing declared TOC
            # must equal its source bytes with only the explicit lexical href
            # edits and relocation-reference edits applied by the plan.
            expected_toc_contents = {}
            for source_toc_path in declared_toc_paths:
                destination = relocations.get(source_toc_path, source_toc_path)
                if destination in expected_contents:
                    expected_toc_contents[destination] = expected_contents[destination]
            expected_toc_target_identities = _toc_target_identities_from_source(
                opf_path, opf_bytes, source_contents,
                planned_edits=planned_toc_edits, relocations=relocations)
            comment = archive.comment

        descriptor, temporary_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)),
            prefix="." + os.path.basename(path) + ".",
            suffix=".normalize.tmp",
        )
        os.close(descriptor)
        _write_archive(temporary_path, entries, comment)
        _validate_normalized_archive(
            temporary_path, expected_span_counts, expected_toc_contents,
            expected_toc_target_identities)
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


def normalize_kepub_package(path, *, split_chapters=False):
    """Normalize one KEPUB and optionally split multi-chapter spine documents.

    Chapter splitting is deliberately opt-in. The default is used by the
    versioned existing-library repair task, where changing spine filenames can
    orphan annotations already stored on a Kobo. New-book entry points pass
    ``split_chapters=True`` before a device has received the package.

    Return ``True`` when either stage changed the archive, ``False`` for a
    byte-identical no-op, and ``None`` when either requested stage failed. Each
    stage is atomic, and its own failure leaves the archive passed to that stage
    untouched.
    """
    normalized = _normalize_kepub_package_only(path)
    if normalized is None or not split_chapters:
        return normalized

    # Lazy import avoids a module cycle: the lexical splitter reuses the
    # normalizer's bounded package/reference primitives.
    from .kepub_spine_splitter import split_multichapter_documents

    split = split_multichapter_documents(path)
    if split is None:
        return None
    return bool(normalized or split)


def kepub_package_needs_normalization(path):
    """Bounded read-only probe for package paths and redundant TOC fragments.

    The probe runs the same fragment-rewrite planner as normalization so its
    answer includes the planner's proof and every conservative skip decision.
    It decompresses each declared TOC at most once, bounded by
    ``MAX_TOC_DOCUMENT_BYTES``, and each eligible distinct fragment-targeted
    content document at most once, bounded by ``MAX_CONTENT_DOCUMENT_BYTES``
    and retained only in the planner's per-call cache.

    Only this normalizer's explicit ``UnsupportedKepubPackage`` refusals are
    terminal. ZIP, parser, decoding, EOF, I/O, and unexpected failures are
    retryable because a short network-share read can surface as any of them.

    The repair task may cache an explicit unsupported result using stat fields.
    That cache is deliberately best-effort, not proof of content identity: an
    undetected stat collision can defer one book until a future repair-version
    bump. This is preferred to hashing a package we may not safely read.
    """
    path = os.fspath(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _reject_oversized_archive(infos)
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise UnsupportedKepubPackage(
                    "KEPUB contains duplicate ZIP member names")
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            relocations = _plan_relocations(opf_path, opf_bytes, set(names))
            toc_rewrites, _planned_toc_edits = _plan_toc_fragment_rewrites(
                archive, opf_path, opf_bytes)
            # Convergence invariant: the probe must never report work for a
            # package that these same planners would decline to change.
            needs_normalization = bool(relocations or toc_rewrites)
            return KepubPackageInspection(
                PROBE_NEEDS_NORMALIZATION if needs_normalization else PROBE_CLEAN)
    except UnsupportedKepubPackage as error:
        log.warning("KEPUB package %s is unsupported by the normalizer: %s", path, error)
        return KepubPackageInspection(PROBE_UNSUPPORTED, str(error))
    except Exception as error:
        log.warning("Could not inspect KEPUB package %s; will retry: %s", path, error)
        return KepubPackageInspection(PROBE_RETRYABLE, str(error))
