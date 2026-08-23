# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Split fragment-addressed KEPUB chapters without changing KoboSpan anchors."""

from bisect import bisect_right
from collections import Counter
import copy
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from urllib.parse import quote, unquote, urlunsplit

from lxml import etree

from .. import logger
from .kepub_package_normalizer import (
    MAX_CONTENT_DOCUMENT_BYTES,
    MAX_PACKAGE_DOCUMENT_BYTES,
    MAX_TOC_DOCUMENT_BYTES,
    UnsupportedKepubPackage,
    _CSS_URL_RE,
    _REFERENCE_ATTRIBUTE_RE,
    _TEXT_DOCUMENT_SUFFIXES,
    _XML_ATTRIBUTE_RE,
    _XML_PARSER,
    _contained_toc_target,
    _package_document_path,
    _read_bounded_member,
    _reject_oversized_archive,
    _resolve_reference,
    _split_local_reference,
    _toc_documents,
    _toc_target_elements,
    _write_archive,
    _xml_start_tag_ranges,
)


__all__ = ["split_multichapter_documents"]

log = logger.create()

_ELEMENT_NAME_RE = re.compile(
    rb"<\s*(?P<name>(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*)")
_CLOSE_ELEMENT_NAME_RE = re.compile(
    rb"</\s*(?P<name>(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*)")
_KOBO_SPAN_CLASS = re.compile(rb"(?:^|\s)koboSpan(?:\s|$)")


#: A real book has tens of chapters in one file, not thousands. The byte bounds
#: inherited from the normalizer cap the INPUT, and they were written for a
#: rewrite — "this implementation temporarily holds the source members, rewritten
#: members, output ZIP and validation members at the same time". A SPLIT is a
#: fan-out, not a rewrite, so an input-side bound stops bounding the peak: every
#: boundary gets its own copy of the document shell.
#:
#: MEASURED on this branch before the cap existed: a 14.9 KB upload with 1000
#: anchors and a 180 KB shell reached 348 MiB of allocation — past the 256 MiB
#: the module declares — and 8000 anchors burned 185 seconds of CPU inside the
#: Flask request handler, on a gevent server where one busy greenlet blocks every
#: other request. The same files with splitting off: 0.29 s and 10.7 MiB.
MAX_SPLIT_PIECES = 512

#: Ceiling on what a single document's split may allocate. Each piece carries the
#: whole shell, so the cost is (pieces × shell) and neither factor is bounded on
#: its own. Checked BEFORE any piece is built.
MAX_SPLIT_PEAK_BYTES = 64 * 1024 * 1024


class _UnsafeSplit(ValueError):
    """A valid package whose split cannot be proven reference-safe."""


def _attributes(start_tag):
    return {
        match.group("name").rsplit(b":", 1)[-1].lower(): match.group("value")
        for match in _XML_ATTRIBUTE_RE.finditer(start_tag)
    }


def _kobo_span_ids(contents):
    ids = Counter()
    for content in contents.values():
        for start, end in _xml_start_tag_ranges(content):
            tag = content[start:end]
            name = _ELEMENT_NAME_RE.match(tag)
            if name is None or name.group("name").rsplit(b":", 1)[-1].lower() != b"span":
                continue
            attributes = _attributes(tag)
            classes = attributes.get(b"class", b"")
            span_id = attributes.get(b"id")
            if span_id is not None and _KOBO_SPAN_CLASS.search(classes):
                ids[span_id] += 1
    return ids


def _encoded_relative_path(target, document_path):
    base = posixpath.dirname(document_path) or "."
    return quote(posixpath.relpath(target, base), safe="/@:+!$&'()*+,;=-._~")


def _unique_piece_names(source, count, occupied):
    directory = posixpath.dirname(source)
    basename = posixpath.basename(source)
    stem, extension = posixpath.splitext(basename)
    names = []
    for piece_number in range(1, count + 1):
        suffix = piece_number
        while True:
            candidate_name = "{}-split-{}{}".format(stem, suffix, extension)
            candidate = posixpath.join(directory, candidate_name) if directory else candidate_name
            if candidate not in occupied:
                break
            suffix += 1
        names.append(candidate)
        occupied.add(candidate)
    return names


def _unique_id(base, occupied, cursor):
    """Allocate `base`, or `base-1`, `base-2`, ... , recording where it got to.

    `cursor` carries the search position per base ACROSS calls. Without it the
    scan restarts at 1 every time, which is O(N^2) in the number of pieces — two
    million str.format calls at 2000 pieces, measured with cProfile.

    It is passed in rather than kept module-global on purpose: a global would
    grow one entry per distinct id for the life of the process, which is a slow
    leak on a long-running server, and would let one book's ids influence the
    next.
    """
    if base not in occupied:
        occupied.add(base)
        return base
    suffix = cursor.get(base, 1)
    while True:
        candidate = "{}-{}".format(base, suffix)
        suffix += 1
        if candidate not in occupied:
            break
    cursor[base] = suffix
    occupied.add(candidate)
    return candidate


def _element_positions(source, document):
    elements = [element for element in document.iter() if isinstance(element.tag, str)]
    ranges = list(_xml_start_tag_ranges(source))
    if len(elements) != len(ranges):
        raise _UnsafeSplit("XML lexical element order cannot be matched safely")
    return elements, ranges


def _xml_element_tokens(source):
    """Yield lexical element tags as ``(start, end, closing, empty)``.

    This mirrors the normalizer's conservative XML scanner, but includes end
    tags so a parsed element's exact inner byte range can be proved without
    serializing the tree. Comments, CDATA, processing instructions, and
    declarations cannot masquerade as element tags.
    """
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
        end = cursor + 1
        if not declaration:
            tag = source[start:end]
            name_match = (_CLOSE_ELEMENT_NAME_RE if closing else _ELEMENT_NAME_RE).match(tag)
            if name_match is None:
                return
            empty = not closing and bool(re.search(rb"/\s*>$", tag))
            yield start, end, closing, empty
        position = end


def _body_element(document):
    bodies = document.xpath("//*[local-name()='body']")
    if len(bodies) != 1:
        raise _UnsafeSplit("content document does not contain exactly one body")
    return bodies[0]


def _nearest_common_ancestor(elements):
    # Strict ancestors are intentional. If one TOC anchor contains another,
    # treating the outer anchor as its own NCA leaves no child on its ancestor
    # chain to cut at. Their first common *ancestor* projects both anchors to
    # the same child, which is the graceful inseparable case.
    chains = [list(reversed(tuple(element.iterancestors()))) for element in elements]
    common = None
    for candidates in zip(*chains):
        if any(candidate is not candidates[0] for candidate in candidates[1:]):
            break
        common = candidates[0]
    if common is None:
        raise _UnsafeSplit("TOC anchors have no common element ancestor")
    return common


def _container_bounds(source, container, elements, ranges):
    try:
        container_index = elements.index(container)
    except ValueError as error:
        raise _UnsafeSplit("split container has no lexical position") from error
    container_start, inner_start = ranges[container_index]
    tokens = list(_xml_element_tokens(source))
    start_tokens = [token for token in tokens if not token[2]]
    if [(start, end) for start, end, _closing, _empty in start_tokens] != ranges:
        raise _UnsafeSplit("XML lexical token stream cannot be matched safely")
    token_index = next(
        (index for index, token in enumerate(tokens) if token[0] == container_start), None)
    if token_index is None or tokens[token_index][3]:
        raise _UnsafeSplit("split container has no content range")
    depth = 0
    for start, end, closing, empty in tokens[token_index:]:
        if closing:
            depth -= 1
            if depth == 0:
                return container_start, inner_start, start, end
        elif not empty:
            depth += 1
    raise _UnsafeSplit("split container closing tag cannot be matched safely")


def _project_anchor(anchor, container):
    """Return the direct child of *container* containing *anchor*."""
    cut = anchor
    while cut.getparent() is not container:
        cut = cut.getparent()
        if cut is None:
            raise _UnsafeSplit("TOC anchor is not below its split container")
    return cut


def _has_nested_cut(nested_cuts):
    """True when an outer TOC anchor can own a piece before an inner cut."""
    return bool(nested_cuts)


def _descended_cut_plan(source, container, anchors, elements, ranges):
    """Prove and plan the single supported nested-anchor descent.

    The ordinary NCA projection may collapse every target onto one child.  We
    descend into that child only when it is itself exactly one TOC target.  The
    outer target owns the bytes before the first nested boundary; surrounding
    sibling content is assigned to the natural edge pieces by the nested
    partitioner rather than copied into every piece.
    """
    children = {
        _project_anchor(anchor, container) for anchor in anchors.values()
    }
    if len(children) != 1:
        return None
    descended_container = next(iter(children))
    if sum(anchor is descended_container for anchor in anchors.values()) != 1:
        return None
    nested_anchors = {
        fragment: anchor for fragment, anchor in anchors.items()
        if anchor is not descended_container
    }
    if not nested_anchors or any(
            descended_container not in anchor.iterancestors()
            for anchor in nested_anchors.values()):
        return None

    descended_bounds = _container_bounds(
        source, descended_container, elements, ranges)
    _descended_start, descended_inner_start, _descended_inner_end, _descended_end = (
        descended_bounds)

    nested_cuts = {
        ranges[elements.index(_project_anchor(anchor, descended_container))][0]
        for anchor in nested_anchors.values()
    }
    # This is deliberately one level only.  If the nested targets still
    # collapse into one child, arbitrary recursive shell construction would be
    # required and the document remains in its current graceful grouping.
    if not _has_nested_cut(nested_cuts) or min(nested_cuts) <= descended_inner_start:
        return None
    boundaries = [descended_inner_start, *sorted(nested_cuts)]
    return descended_bounds, boundaries


def _anchor_cut_plan(source, document, fragments, elements, ranges):
    body = _body_element(document)
    anchors = {}
    for fragment in fragments:
        matches = [
            element for element in elements
            if element.get("id") == fragment or element.get("name") == fragment
        ]
        if len(matches) != 1:
            raise _UnsafeSplit(
                "TOC fragment {!r} does not identify exactly one element".format(fragment))
        anchor = matches[0]
        if anchor is not body and body not in anchor.iterancestors():
            raise _UnsafeSplit("TOC fragment is outside the content body")
        anchors[fragment] = anchor

    container = _nearest_common_ancestor(list(anchors.values()))
    if container is not body and body not in container.iterancestors():
        raise _UnsafeSplit("TOC anchor common ancestor is outside the content body")
    bounds = _container_bounds(source, container, elements, ranges)
    nested_partition = False
    anchor_positions = {}
    cut_positions = {}
    for fragment, anchor in anchors.items():
        anchor_positions[fragment] = ranges[elements.index(anchor)][0]
        cut = _project_anchor(anchor, container)
        cut_positions[fragment] = ranges[elements.index(cut)][0]
    boundaries = sorted(set(cut_positions.values()))
    if len(boundaries) == 1:
        descended = _descended_cut_plan(
            source, container, anchors, elements, ranges)
        if descended is not None:
            bounds, boundaries = descended
            nested_partition = True
    return bounds, boundaries, anchor_positions, nested_partition


def _partition_document(source, boundaries, container_bounds):
    container_start, container_inner_start, container_inner_end, container_end = container_bounds
    prefix = source[:container_inner_start]
    suffix = source[container_inner_end:]

    # Every piece carries the whole shell, so the peak is (pieces x shell) and
    # neither factor is bounded on its own: a small archive with many anchors and
    # a large <head> multiplies into hundreds of megabytes. Refused here, before
    # the first copy is allocated, rather than discovered after the archive has
    # been built and written.
    projected = len(boundaries) * (len(prefix) + len(suffix))
    if len(boundaries) > MAX_SPLIT_PIECES or projected > MAX_SPLIT_PEAK_BYTES:
        raise _UnsafeSplit(
            "split would allocate {} bytes across {} pieces, over the "
            "{}-byte / {}-piece budget".format(
                projected, len(boundaries), MAX_SPLIT_PEAK_BYTES, MAX_SPLIT_PIECES))
    starts = [container_inner_start] + boundaries[1:]
    ends = boundaries[1:] + [container_inner_end]
    pieces = [prefix + source[start:end] + suffix for start, end in zip(starts, ends)]
    for piece in pieces:
        try:
            etree.fromstring(piece, parser=_XML_PARSER)
        except etree.XMLSyntaxError as error:
            raise _UnsafeSplit(
                "chapter boundary would create a malformed content document") from error
    # Make the variables part of the proof: every shell includes the exact
    # ancestor/open-tag chain through the NCA and its exact closing tag.
    if not all(
            piece[:container_inner_start] == source[:container_inner_start]
            for piece in pieces):
        raise _UnsafeSplit("content document shell changed before split container content")
    if source[container_inner_end:container_end] not in pieces[0]:
        raise _UnsafeSplit("content document shell lost its split container closing tag")
    return pieces


def _partition_nested_document(
        source, boundaries, container_bounds, container, elements, ranges):
    """Partition a nested group without copying its surrounding content.

    The ordinary partition duplicates a full prefix and suffix.  During a
    second pass those shells can contain sibling prose or KoboSpans inherited
    from the first-pass piece.  Keep that content on the natural edge piece and
    build middle shells only from exact lexical ancestor tags.
    """
    body = _body_element(container.getroottree().getroot())
    body_bounds = _container_bounds(source, body, elements, ranges)
    _body_start, body_inner_start, body_inner_end, _body_end = body_bounds
    chain = []
    current = container
    while current is not body:
        chain.append(current)
        current = current.getparent()
        if current is None:
            raise _UnsafeSplit("nested split container is outside the content body")
    chain.reverse()

    element_bounds = {
        element: _container_bounds(source, element, elements, ranges)
        for element in chain
    }
    minimal_prefix = source[:body_inner_start] + b"".join(
        source[element_bounds[element][0]:element_bounds[element][1]]
        for element in chain
    )
    minimal_suffix = b"".join(
        source[element_bounds[element][2]:element_bounds[element][3]]
        for element in reversed(chain)
    ) + source[body_inner_end:]

    _container_start, container_inner_start, container_inner_end, _container_end = (
        container_bounds)
    full_prefix = source[:container_inner_start]
    full_suffix = source[container_inner_end:]
    starts = [container_inner_start] + boundaries[1:]
    ends = boundaries[1:] + [container_inner_end]
    projected = sum(
        (len(full_prefix) if index == 0 else len(minimal_prefix))
        + (end - start)
        + (len(full_suffix) if index == len(starts) - 1 else len(minimal_suffix))
        for index, (start, end) in enumerate(zip(starts, ends))
    )
    if len(starts) > MAX_SPLIT_PIECES or projected > MAX_SPLIT_PEAK_BYTES:
        raise _UnsafeSplit(
            "nested split would allocate {} bytes across {} pieces, over the "
            "{}-byte / {}-piece budget".format(
                projected, len(starts), MAX_SPLIT_PEAK_BYTES, MAX_SPLIT_PIECES))

    pieces = []
    for index, (start, end) in enumerate(zip(starts, ends)):
        prefix = full_prefix if index == 0 else minimal_prefix
        suffix = full_suffix if index == len(starts) - 1 else minimal_suffix
        piece = prefix + source[start:end] + suffix
        try:
            etree.fromstring(piece, parser=_XML_PARSER)
        except etree.XMLSyntaxError as error:
            raise _UnsafeSplit(
                "nested chapter boundary would create malformed XML") from error
        pieces.append(piece)
    return pieces


def _piece_for_position(boundaries, position):
    return max(0, bisect_right(boundaries, position) - 1)


def _prefer_explicit_anchor_pieces(
        mapping, anchor_positions, boundaries, piece_names):
    """Make explicit TOC anchors authoritative over copied shell ids."""
    for fragment, position in anchor_positions.items():
        mapping[fragment] = piece_names[_piece_for_position(boundaries, position)]
    return mapping


def _fragment_piece_map(
        document, elements, ranges, boundaries, piece_names, anchor_positions):
    mapping = {}
    for element, (position, _end) in zip(elements, ranges):
        piece = piece_names[_piece_for_position(boundaries, position)]
        for attribute in ("id", "name"):
            fragment = element.get(attribute)
            if fragment:
                prior = mapping.setdefault(fragment, piece)
                if prior != piece:
                    raise _UnsafeSplit("fragment identity occurs in multiple pieces")
    # A descended container is copied into every piece as lexical shell, so its
    # id is physically repeated.  Explicit TOC anchors are authoritative and
    # are applied last: the outer anchor's original position deliberately owns
    # piece one and cannot be overwritten by a repeated shell id.
    return _prefer_explicit_anchor_pieces(
        mapping, anchor_positions, boundaries, piece_names)


def _should_refine_nested_piece(fragments):
    """True when one first-pass piece still owns multiple TOC chapters."""
    return len(fragments) >= 2


def _refine_nested_pieces(pieces, fragments, fragment_indexes):
    """Split chapter groups left together by the first lexical partition.

    The first NCA partition remains authoritative for piece order.  A resulting
    piece that still owns multiple explicit TOC fragments gets one more lexical
    planning pass against its own well-formed XML.  This handles a nested group
    beside ordinary top-level chapters without mixing cut containers or
    synthesizing ancestor tags.
    """
    refinements = {}
    for piece_index, piece in enumerate(pieces):
        owned = {
            fragment for fragment in fragments
            if fragment_indexes[fragment] == piece_index
        }
        if not _should_refine_nested_piece(owned):
            continue
        document = etree.fromstring(piece, parser=_XML_PARSER)
        elements, ranges = _element_positions(piece, document)
        container_bounds, boundaries, anchor_positions, _nested_partition = _anchor_cut_plan(
            piece, document, owned, elements, ranges)
        if len(boundaries) < 2:
            continue
        container_start = container_bounds[0]
        container = next(
            (element for element, (start, _end) in zip(elements, ranges)
             if start == container_start), None)
        if container is None:
            raise _UnsafeSplit("nested split container has no lexical element")
        refined = _partition_nested_document(
            piece, boundaries, container_bounds, container, elements, ranges)
        local_indexes = _fragment_piece_map(
            document, elements, ranges, boundaries,
            list(range(len(refined))), anchor_positions)
        refinements[piece_index] = refined, local_indexes

    if not refinements:
        return pieces, fragment_indexes, list(range(len(pieces)))

    flattened = []
    name_slots = []
    first_indexes = {}
    next_extra_slot = len(pieces)
    for piece_index, piece in enumerate(pieces):
        first_indexes[piece_index] = len(flattened)
        refinement = refinements.get(piece_index)
        if refinement is None:
            flattened.append(piece)
            name_slots.append(piece_index)
            continue
        flattened.extend(refinement[0])
        # The first refined child retains the name this first-pass piece had.
        # Extra children use names after every first-pass name, so refining an
        # early group cannot rename later chapters that already split safely.
        name_slots.append(piece_index)
        extra_count = len(refinement[0]) - 1
        name_slots.extend(range(next_extra_slot, next_extra_slot + extra_count))
        next_extra_slot += extra_count
    if (len(flattened) > MAX_SPLIT_PIECES
            or sum(map(len, flattened)) > MAX_SPLIT_PEAK_BYTES):
        raise _UnsafeSplit(
            "nested split exceeds the {}-byte / {}-piece budget".format(
                MAX_SPLIT_PEAK_BYTES, MAX_SPLIT_PIECES))

    final_indexes = {}
    for fragment, piece_index in fragment_indexes.items():
        destination = first_indexes[piece_index]
        refinement = refinements.get(piece_index)
        if refinement is not None:
            destination += refinement[1].get(fragment, 0)
        final_indexes[fragment] = destination
    return flattened, final_indexes, name_slots


def _replacement_for_reference(value, document_path, split_plans):
    try:
        split = _split_local_reference(value)
    except (TypeError, ValueError, UnsupportedKepubPackage) as error:
        raise _UnsafeSplit("local reference cannot be resolved safely") from error
    if split is None:
        return value
    parts, reference_path = split
    if not reference_path and not parts.fragment:
        return value
    resolved = (document_path if not reference_path
                else _resolve_reference(document_path, reference_path))
    plan = split_plans.get(resolved)
    if plan is None:
        return value
    if parts.fragment:
        fragment = unquote(parts.fragment)
        destination = plan["fragment_pieces"].get(fragment)
        if destination is None:
            raise _UnsafeSplit(
                "reference to split document has an unknown fragment: {!r}".format(fragment))
    else:
        destination = plan["piece_names"][0]
    rewritten_path = _encoded_relative_path(destination, document_path)
    return urlunsplit(("", "", rewritten_path, parts.query, parts.fragment))


def _rewrite_references(source, old_document, new_document, split_plans):
    def rewritten_value(match):
        value = match.group("value").decode("utf-8")
        rewritten = _replacement_for_reference(value, old_document, split_plans)
        if rewritten == value and old_document == new_document:
            return None
        if rewritten == value:
            try:
                local = _split_local_reference(value)
            except (TypeError, ValueError, UnsupportedKepubPackage) as error:
                raise _UnsafeSplit("reference cannot be rebased safely") from error
            if local is None or not local[1]:
                return None
            target = _resolve_reference(old_document, local[1])
            rewritten = urlunsplit((
                "", "", _encoded_relative_path(target, new_document),
                local[0].query, local[0].fragment,
            ))
        return rewritten.encode("utf-8")

    def replace_attribute(match):
        rewritten = rewritten_value(match)
        if rewritten is None:
            return match.group(0)
        return (match.group("prefix") + match.group("quote")
                + rewritten + match.group("quote"))

    def replace_css_url(match):
        rewritten = rewritten_value(match)
        if rewritten is None:
            return match.group(0)
        return (match.group("prefix") + match.group("quote") + rewritten
                + match.group("quote") + match.group("suffix"))

    rewritten = _REFERENCE_ATTRIBUTE_RE.sub(replace_attribute, source)
    if old_document.lower().endswith(".css"):
        rewritten = _CSS_URL_RE.sub(replace_css_url, rewritten)
    return rewritten


def _rewrite_selected_attributes(source, replacements):
    pending = dict(replacements)
    byte_replacements = []
    for element_index, (start, end) in enumerate(_xml_start_tag_ranges(source)):
        tag = source[start:end]
        for match in _XML_ATTRIBUTE_RE.finditer(tag):
            local_name = match.group("name").rsplit(b":", 1)[-1].decode(
                "ascii", errors="ignore").lower()
            key = (element_index, local_name)
            if key not in pending:
                continue
            value_start = start + match.start("value")
            byte_replacements.append((
                value_start,
                value_start + len(match.group("value")),
                pending.pop(key).encode("utf-8"),
            ))
    if pending:
        raise _UnsafeSplit("a selected TOC attribute could not be edited lexically")
    rewritten = source
    for start, end, replacement in reversed(byte_replacements):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    return rewritten


def _collect_toc_targets(archive, opf_path, opf_bytes):
    targets = []
    parsed = {}
    for toc_path, kind in _toc_documents(opf_path, opf_bytes):
        key = (toc_path, kind)
        if key in parsed:
            document, toc_bytes, element_indexes = parsed[key]
        else:
            try:
                toc_bytes = _read_bounded_member(
                    archive, toc_path, MAX_TOC_DOCUMENT_BYTES,
                    "{} TOC document".format(kind))
                document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
            except (etree.XMLSyntaxError, UnsupportedKepubPackage) as error:
                raise _UnsafeSplit("declared TOC cannot be parsed completely") from error
            element_indexes = {
                element: index for index, element in enumerate(
                    node for node in document.iter() if isinstance(node.tag, str))
            }
            parsed[key] = document, toc_bytes, element_indexes
        for element, attribute in _toc_target_elements(document, kind):
            value = element.get(attribute)
            try:
                target = _contained_toc_target(toc_path, value)
            except (TypeError, ValueError, UnsupportedKepubPackage) as error:
                raise _UnsafeSplit("TOC target cannot be contained safely") from error
            if target is None:
                continue
            parts, resolved = target
            if not parts.path:
                resolved = toc_path
            targets.append({
                "toc_path": toc_path,
                "kind": kind,
                "element_index": element_indexes[element],
                "attribute": attribute,
                "value": value,
                "parts": parts,
                "resolved": resolved,
                "fragment": unquote(parts.fragment),
            })
    return targets, parsed


def _split_candidates(targets):
    fragments = {}
    for target in targets:
        if target["fragment"]:
            fragments.setdefault(target["resolved"], set()).add(target["fragment"])
    # An absurd fragment count is the fan-out attack, not a book. Dropping the
    # document leaves it unsplit — the behaviour before this feature — rather than
    # failing the whole package, so one hostile document cannot deny the split to
    # the rest of a legitimate book.
    candidates = {}
    for document, values in fragments.items():
        if len(values) < 2:
            continue
        if len(values) > MAX_SPLIT_PIECES:
            log.info(
                "KEPUB spine split skipped for %s: %d TOC fragments exceed the "
                "%d-piece cap", document, len(values), MAX_SPLIT_PIECES)
            continue
        candidates[document] = values
    return candidates


def _manifest_and_spine(package, opf_path):
    if package.xpath("//*[@xml:base]", namespaces={
            "xml": "http://www.w3.org/XML/1998/namespace"}):
        raise _UnsafeSplit("package xml:base prevents reference-safe splitting")
    manifests = package.xpath("//*[local-name()='manifest']")
    spines = package.xpath("//*[local-name()='spine']")
    if len(manifests) != 1 or len(spines) != 1:
        raise _UnsafeSplit("package does not contain exactly one manifest and spine")
    items = list(manifests[0].xpath("./*[local-name()='item']"))
    itemrefs = list(spines[0].xpath("./*[local-name()='itemref']"))
    item_by_id = {}
    path_by_id = {}
    for item in items:
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or item_id in item_by_id or not href:
            continue
        split = _split_local_reference(href)
        if split is None:
            continue
        item_by_id[item_id] = item
        path_by_id[item_id] = _resolve_reference(opf_path, split[1])
    spine_paths = []
    for itemref in itemrefs:
        item_id = itemref.get("idref")
        if item_id not in path_by_id:
            raise _UnsafeSplit("spine itemref does not resolve to one manifest item")
        spine_paths.append(path_by_id[item_id])
    if len(spine_paths) != len(set(spine_paths)):
        raise _UnsafeSplit("source spine contains a content document more than once")
    return manifests[0], spines[0], item_by_id, path_by_id, spine_paths


def _plan_splits(archive, opf_path, opf_bytes, candidates, archive_names):
    package = etree.fromstring(opf_bytes, parser=_XML_PARSER)
    manifest, spine, item_by_id, path_by_id, source_spine = _manifest_and_spine(
        package, opf_path)
    occupied_names = set(archive_names)
    occupied_ids = set(item_by_id)
    plans = {}
    for document_path, fragments in candidates.items():
        manifest_ids = [item_id for item_id, path in path_by_id.items() if path == document_path]
        if len(manifest_ids) != 1 or source_spine.count(document_path) != 1:
            raise _UnsafeSplit(
                "split target must be one manifest item occurring once in the spine")
        item_id = manifest_ids[0]
        item = item_by_id[item_id]
        itemrefs = spine.xpath("./*[local-name()='itemref'][@idref=$item_id]", item_id=item_id)
        if len(itemrefs) != 1 or itemrefs[0].get("id") is not None:
            raise _UnsafeSplit("split spine itemref identity cannot be expanded safely")
        source = _read_bounded_member(
            archive, document_path, MAX_CONTENT_DOCUMENT_BYTES, "split content document")
        document = etree.fromstring(source, parser=_XML_PARSER)
        if document.xpath("//*[@xml:base]", namespaces={
                "xml": "http://www.w3.org/XML/1998/namespace"}):
            raise _UnsafeSplit("xml:base prevents reference-safe splitting")
        if document.xpath("//*[local-name()='base'][@href]"):
            raise _UnsafeSplit("HTML base href prevents reference-safe splitting")
        elements, ranges = _element_positions(source, document)
        container_bounds, boundaries, anchor_positions, nested_partition = _anchor_cut_plan(
            source, document, fragments, elements, ranges)
        # Several TOC anchors may live in one direct child of the common
        # ancestor. Preserve every distinct first-pass boundary, then refine
        # any still-collapsed piece with its own lexical container below.
        if len(boundaries) < 2:
            continue
        ordered_fragments = sorted(fragments, key=anchor_positions.__getitem__)
        if nested_partition:
            container_start = container_bounds[0]
            container = next(
                (element for element, (start, _end) in zip(elements, ranges)
                 if start == container_start), None)
            if container is None:
                raise _UnsafeSplit("nested split container has no lexical element")
            pieces = _partition_nested_document(
                source, boundaries, container_bounds, container, elements, ranges)
        else:
            pieces = _partition_document(source, boundaries, container_bounds)
        fragment_indexes = _fragment_piece_map(
            document, elements, ranges, boundaries,
            list(range(len(pieces))), anchor_positions)
        pieces, fragment_indexes, name_slots = _refine_nested_pieces(
            pieces, fragments, fragment_indexes)
        allocated_names = _unique_piece_names(
            document_path, len(pieces), occupied_names)
        piece_names = [allocated_names[slot] for slot in name_slots]
        fragment_pieces = {
            fragment: piece_names[index]
            for fragment, index in fragment_indexes.items()
        }
        plans[document_path] = {
            "source": source,
            "manifest_id": item_id,
            "manifest_item": item,
            "itemref": itemrefs[0],
            "ordered_fragments": ordered_fragments,
            "boundaries": boundaries,
            "piece_names": piece_names,
            "pieces": pieces,
            "fragment_pieces": fragment_pieces,
        }

    # Scoped to this package, so nothing survives into the next one.
    id_cursor = {}
    for plan in plans.values():
        item = plan["manifest_item"]
        itemref = plan["itemref"]
        piece_ids = [plan["manifest_id"]] + [
            _unique_id(plan["manifest_id"] + "-split", occupied_ids, id_cursor)
            for _piece in plan["piece_names"][1:]
        ]
        for index, (piece_name, piece_id) in enumerate(zip(plan["piece_names"], piece_ids)):
            new_item = copy.deepcopy(item)
            new_item.set("id", piece_id)
            new_item.set("href", _encoded_relative_path(piece_name, opf_path))
            item.addprevious(new_item)
            new_itemref = copy.deepcopy(itemref)
            new_itemref.set("idref", piece_id)
            itemref.addprevious(new_itemref)
            if index == 0:
                new_item.tail = item.tail
                new_itemref.tail = itemref.tail
        manifest.remove(item)
        spine.remove(itemref)
        plan["piece_ids"] = piece_ids

    opf_rewritten = etree.tostring(
        package.getroottree(), encoding="utf-8",
        xml_declaration=opf_bytes.lstrip().startswith(b"<?xml"))
    return plans, opf_rewritten, source_spine


def _toc_rewrites(targets, parsed_tocs, split_plans):
    replacements_by_toc = {}
    for target in targets:
        plan = split_plans.get(target["resolved"])
        if plan is None:
            continue
        if target["fragment"]:
            destination = plan["fragment_pieces"].get(target["fragment"])
            if destination is None:
                raise _UnsafeSplit("TOC fragment has no destination piece")
        else:
            destination = plan["piece_names"][0]
        value = urlunsplit((
            "", "", _encoded_relative_path(destination, target["toc_path"]),
            target["parts"].query, "",
        ))
        replacements_by_toc.setdefault(target["toc_path"], {})[
            (target["element_index"], target["attribute"])
        ] = value

    source_by_toc = {}
    for (toc_path, _kind), (_document, source, _indexes) in parsed_tocs.items():
        prior = source_by_toc.setdefault(toc_path, source)
        if prior != source:
            raise _UnsafeSplit("one TOC path yielded inconsistent source bytes")
    return {
        toc_path: _rewrite_selected_attributes(source_by_toc[toc_path], replacements)
        for toc_path, replacements in replacements_by_toc.items()
    }


def _unknown_reference_mentions(contents, split_plans):
    """Reject raw path mentions outside href/src and CSS url attributes."""
    for member, source in contents.items():
        if not member.lower().endswith(_TEXT_DOCUMENT_SUFFIXES):
            continue
        recognized = bytearray(source)
        for regex in (_REFERENCE_ATTRIBUTE_RE, _CSS_URL_RE):
            for match in regex.finditer(source):
                start, end = match.span("value")
                recognized[start:end] = b" " * (end - start)
        for original in split_plans:
            relative = _encoded_relative_path(original, member)
            spellings = {
                original,
                unquote(original),
                quote(unquote(original), safe="/"),
                relative,
                unquote(relative),
                posixpath.basename(original),
            }
            for spelling in spellings:
                token = spelling.encode("utf-8")
                if token and token in recognized:
                    raise _UnsafeSplit(
                        "split document path occurs outside a supported reference attribute")


def _build_entries(infos, contents, opf_path, opf_rewritten, toc_rewrites, split_plans):
    rewritten = dict(contents)
    rewritten[opf_path] = opf_rewritten
    rewritten.update(toc_rewrites)
    _unknown_reference_mentions(rewritten, split_plans)

    for name, source in list(rewritten.items()):
        if name in split_plans or not name.lower().endswith(_TEXT_DOCUMENT_SUFFIXES):
            continue
        rewritten[name] = _rewrite_references(source, name, name, split_plans)

    entries = []
    for info in infos:
        name = info.filename
        plan = split_plans.get(name)
        if plan is None:
            entries.append((copy.copy(info), rewritten[name]))
            continue
        for piece_name, piece in zip(plan["piece_names"], plan["pieces"]):
            piece = _rewrite_references(piece, name, piece_name, split_plans)
            new_info = copy.copy(info)
            new_info.filename = piece_name
            new_info.orig_filename = piece_name
            entries.append((new_info, piece))
    return entries


def _spine_paths(opf_path, opf_bytes):
    package = etree.fromstring(opf_bytes, parser=_XML_PARSER)
    _manifest, _spine, _items, path_by_id, paths = _manifest_and_spine(package, opf_path)
    if any(path not in path_by_id.values() for path in paths):
        raise ValueError("spine contains an unresolved content document")
    return paths


def _validate_split_archive(
        path, source_contents, expected_contents, split_plans, source_spine,
        expected_span_ids, expected_comment):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        _reject_oversized_archive(infos)
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("split KEPUB contains duplicate ZIP member names")
        if not infos or infos[0].filename != "mimetype":
            raise ValueError("mimetype is not the first EPUB entry")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("mimetype is compressed")
        if archive.read(infos[0]) != b"application/epub+zip":
            raise ValueError("mimetype has unexpected content")
        if archive.testzip() is not None:
            raise ValueError("split KEPUB failed its CRC check")
        if archive.comment != expected_comment:
            raise ValueError("archive comment changed during split")
        actual = {info.filename: archive.read(info) for info in infos}
        if actual != expected_contents:
            raise ValueError("split archive differs from the exact rewrite plan")
        if _kobo_span_ids(actual) != expected_span_ids:
            raise ValueError("KoboSpan id multiset changed during spine split")
        opf_path = _package_document_path(archive)
        opf_bytes = _read_bounded_member(
            archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
        actual_spine = _spine_paths(opf_path, opf_bytes)

        expected_spine = []
        for source in source_spine:
            plan = split_plans.get(source)
            expected_spine.extend(plan["piece_names"] if plan else [source])
        if actual_spine != expected_spine or len(actual_spine) != len(set(actual_spine)):
            raise ValueError("spine reading order changed during split")

        for toc_path, kind in _toc_documents(opf_path, opf_bytes):
            toc = etree.fromstring(actual[toc_path], parser=_XML_PARSER)
            for element, attribute in _toc_target_elements(toc, kind):
                target = _contained_toc_target(toc_path, element.get(attribute))
                if target is None:
                    continue
                parts, resolved = target
                if not parts.path:
                    resolved = toc_path
                if resolved in {
                        piece for plan in split_plans.values()
                        for piece in plan["piece_names"]} and parts.fragment:
                    raise ValueError("TOC target retains a fragment for a split document")

        touched = {opf_path, *split_plans}
        touched.update(
            name for name in source_contents
            if expected_contents.get(name) != source_contents[name])
        for name, source in source_contents.items():
            if name not in touched and actual.get(name) != source:
                raise ValueError("non-touched ZIP member changed: " + name)


def split_multichapter_documents(path):
    """Atomically split content documents targeted by multiple TOC fragments.

    Return ``True`` when the archive was rewritten, ``False`` when no provably
    safe split is available, and ``None`` on processing or validation failure.
    No exception escapes and the source path is replaced only after validation.
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
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            try:
                targets, parsed_tocs = _collect_toc_targets(archive, opf_path, opf_bytes)
                candidates = _split_candidates(targets)
                if not candidates:
                    return False
                plans, opf_rewritten, source_spine = _plan_splits(
                    archive, opf_path, opf_bytes, candidates, set(names))
                if not plans:
                    return False
                toc_rewrites = _toc_rewrites(targets, parsed_tocs, plans)
                if archive.testzip() is not None:
                    raise ValueError("KEPUB failed its CRC check")
                source_contents = {info.filename: archive.read(info) for info in infos}
                entries = _build_entries(
                    infos, source_contents, opf_path, opf_rewritten, toc_rewrites, plans)
            except _UnsafeSplit as error:
                log.info("KEPUB spine split skipped for %s: %s", path, error)
                return False
            expected_span_ids = _kobo_span_ids(source_contents)
            expected_contents = {info.filename: content for info, content in entries}
            comment = archive.comment

        descriptor, temporary_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)),
            prefix="." + os.path.basename(path) + ".",
            suffix=".spine-split.tmp",
        )
        os.close(descriptor)
        _write_archive(temporary_path, entries, comment)
        _validate_split_archive(
            temporary_path, source_contents, expected_contents, plans, source_spine,
            expected_span_ids, comment)
        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        os.replace(temporary_path, path)
        temporary_path = None
        return True
    except Exception as error:
        log.warning("Could not split KEPUB spine %s; original preserved: %s", path, error)
        return None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


#: The shape `_unique_piece_names` gives a piece. Exposed so a caller can ask
#: "did WE split this package?" without duplicating the pattern.
_PIECE_NAME_RE = re.compile(rb"-split-\d+$")


def package_was_split_by_us(path):
    """True when this package's SPINE contains documents our splitter produced.

    Answers one question and one only: is re-splitting a replacement for this
    package the anchor-preserving choice?

    It matters because piece naming is deterministic (see
    tests/unit/test_split_piece_names_are_deterministic.py). So when the stored
    package is already split:

      * a replacement built from the same source re-splits to the SAME names, so
        splitting PRESERVES an annotation anchored to `X-split-2.xhtml` while
        withholding the split deletes the file that anchor names;
      * a replacement built from a different edition breaks those anchors
        whatever we do.

    Splitting is therefore never worse than withholding it for an
    already-split package, and strictly better in the common case. When the
    stored package was NOT split the reverse holds, which is why the caller
    still withholds the split there.

    Deliberately checks the SPINE rather than merely scanning member names: a
    file called `chapter-split-1.xhtml` that no itemref references proves
    nothing, and a false positive here re-introduces exactly the harm the
    caller's guard exists to prevent. Any failure to read the package answers
    False — the conservative direction.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            opf_path = _package_document_path(archive)
            opf_bytes = _read_bounded_member(
                archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
            for href in _spine_paths(opf_path, opf_bytes):
                stem = posixpath.splitext(posixpath.basename(href))[0]
                if _PIECE_NAME_RE.search(stem.encode("utf-8")):
                    return True
    except Exception:
        log.info("Could not inspect %s for existing spine splits", path)
        return False
    return False
