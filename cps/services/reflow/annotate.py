# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Annotate unresolved source readings after resolved markers are accounted for."""

import html as _html
import re

#: The class the EPUB stylesheet styles and a reader can restyle or hide.
CLASS = "reflow-uncertain"

_TAG = re.compile(r"<[^>]*>", re.S)
_SPAN_OPEN = re.compile(r"<\s*span\b", re.I)
_SPAN_CLOSE = re.compile(r"<\s*/\s*span\s*>", re.I)
_SELF_CLOSING = re.compile(r"/\s*>$")
_IS_ANNOTATION = re.compile(r'class\s*=\s*"[^"]*\bsource-reading-annotation\b', re.I)
_IS_UNCERTAIN = re.compile(r'class\s*=\s*"[^"]*\b%s\b' % CLASS, re.I)
#: One character of readable text: an entity reference, or a character that is
#: already itself. Matching entities whole is what lets a token containing "&" be
#: found inside markup that spells it "&amp;".
_DIGITS = re.compile(r"\d+")
_UNIT = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});|.", re.S)

#: Where a model puts the damaged token, in the order we trust them. ``reading``
#: is last because a model that uses that key usually means its *proposed* reading.
_TOKEN_KEYS = ("token", "text", "word", "original", "reading")
_CANDIDATE_KEYS = ("candidates", "alternatives", "suggestions", "readings", "likely")


def uncertain_record(item):
    """One flagged reading, in the one shape the rest of the pipeline reads.

    The prompt asks for ``{"token": ..., "candidates": [...]}`` and that is what the
    default model returns, but the shape is a model's opinion and the cost of being
    wrong about it is silent: a record nobody can read is a reading the book never
    mentions. Returns ``None`` for anything that does not name a token.
    """
    if isinstance(item, str):
        token, candidates = item, []
        extras = {}
    elif isinstance(item, dict):
        token = next((str(item[key]) for key in _TOKEN_KEYS
                      if isinstance(item.get(key), str) and item[key].strip()), "")
        raw = next((item[key] for key in _CANDIDATE_KEYS
                    if item.get(key) not in (None, "", [], {})), [])
        if isinstance(raw, str):
            raw = [raw]
        candidates = [str(one) for one in raw if str(one).strip()] \
            if isinstance(raw, (list, tuple)) else []
        # Extra keys ride along: a source-evidence record's score and where on
        # the printed page it points are reportable, not noise.
        extras = {key: value for key, value in item.items()
                  if key not in _TOKEN_KEYS + _CANDIDATE_KEYS}
    else:
        return None
    token = token.strip()
    if not token:
        return None
    return dict(extras, token=token, candidates=candidates)


def uncertain_readings(span):
    """The damaged token, the likeliest reading of it, and the rest.

    Models routinely repeat the token among its own candidates (``"4s"`` ->
    ``["45", "4s"]``). A candidate identical to the token tells a reader nothing,
    so it is not offered as a correction — and if that leaves nothing, the token is
    still flagged, because "this is damaged and I cannot better it" is information.
    """
    if not isinstance(span, dict):
        return "", []
    token = str(span.get("token") or "")
    seen = {token}
    readings = []
    for candidate in span.get("candidates") or []:
        candidate = str(candidate)
        if candidate and candidate not in seen:
            seen.add(candidate)
            readings.append(candidate)
    return token, readings


def _title(readings):
    if not readings:
        return "uncertain reading"
    title = "likely: %s" % readings[0]
    if len(readings) > 1:
        title += "; also: %s" % ", ".join(readings[1:])
    return title


def _attr(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


class _TextNode(object):
    """One run of readable text, and the way back to the bytes it came from.

    ``plain`` is what a reader sees; ``starts`` and ``ends`` say which slice of the
    original markup each of its characters came from, so a match found in readable
    text can be wrapped without the escaped source ever being rewritten.
    """

    __slots__ = ("raw_start", "plain", "starts", "ends", "opens", "closes", "taken")

    def __init__(self, html, raw_start, raw_end):
        self.raw_start = raw_start
        self.taken = []
        plain, starts, ends, opens, closes = [], [], [], [], []
        for unit in _UNIT.finditer(html, raw_start, raw_end):
            piece = _html.unescape(unit.group(0))
            for index, char in enumerate(piece):
                plain.append(char)
                starts.append(unit.start())
                ends.append(unit.end())
                opens.append(index == 0)
                closes.append(index == len(piece) - 1)
        self.plain = "".join(plain)
        self.starts, self.ends = starts, ends
        self.opens, self.closes = opens, closes

    def matches(self, token):
        """Where *token* sits in this node, as a slice of the markup to wrap.

        The slice always covers whole words. A model quoting a damaged word drops
        the sentence furniture around it — it flags ``wel1`` on a page that prints
        ``wel1.`` — so the slice may widen outwards, but only across characters
        that are neither a letter nor a digit. Everything alphanumeric under the
        highlight is therefore exactly what the model flagged, and both edges land
        on whitespace, which is where the word gate's boundaries already are.
        Widening that cannot reach whitespace without crossing a letter or a digit
        is not widening, it is annexing a different word, and this returns ``None``.
        """
        width = len(token)
        at = self.plain.find(token)
        while at != -1:
            span = self._widen(at, at + width)
            if span is not None and not self._overlaps(*span):
                yield span, (self.starts[span[0]], self.ends[span[1] - 1])
            at = self.plain.find(token, at + 1)

    def find(self, token):
        found = next(self.matches(token), None)
        if found is not None:
            span, raw = found
            self.taken.append(span)
            return raw
        return None

    def _widen(self, at, end):
        """Grow the slice out to whitespace across punctuation only, or give up."""
        plain = self.plain
        while at > 0 and not plain[at - 1].isspace() and not plain[at - 1].isalnum():
            at -= 1
        while end < len(plain) and not plain[end].isspace() and not plain[end].isalnum():
            end += 1
        if at > 0 and not plain[at - 1].isspace():
            return None
        if end < len(plain) and not plain[end].isspace():
            return None
        if not (self.opens[at] and self.closes[end - 1]):
            return None
        return at, end

    def _overlaps(self, at, end):
        return any(at < taken_end and taken_at < end for taken_at, taken_end in self.taken)


def _text_nodes(html, *, include_uncertain=False):
    """The readable runs of *html*, skipping anything already marked uncertain."""
    nodes, spans, cursor = [], [], 0
    for tag in _TAG.finditer(html):
        if tag.start() > cursor and not any(s[1] for s in spans) and (include_uncertain or not any(s[0] for s in spans)):
            nodes.append(_TextNode(html, cursor, tag.start()))
        cursor = tag.end()
        text = tag.group(0)
        if _SPAN_CLOSE.match(text):
            if spans:
                spans.pop()
        elif _SPAN_OPEN.match(text) and not _SELF_CLOSING.search(text):
            spans.append((bool(_IS_UNCERTAIN.search(text)), bool(_IS_ANNOTATION.search(text))))
    if cursor < len(html) and not any(s[1] for s in spans) and (include_uncertain or not any(s[0] for s in spans)):
        nodes.append(_TextNode(html, cursor, len(html)))
    return nodes


def mark_uncertain(html, spans, *, source_ranges=None):
    """Wrap each flagged reading where it stands. Returns ``(html, placed)``.

    Nothing between the wrapped tags is rewritten — not the escaping, not the
    characters — so the page still says exactly what it said. A reading that cannot
    be placed this way is not placed at all; the records that were are returned, so
    a caller can tell a reading the reader can see from one the conversion could
    only describe.
    """
    if not html or not spans:
        return html or "", []

    nodes = _text_nodes(html)
    complete_nodes = _text_nodes(html, include_uncertain=True)
    edits, placed = [], []
    for index, span in enumerate(spans):
        record = uncertain_record(span)
        if record is None:
            continue
        token, readings = uncertain_readings(record)
        if 'source_bbox' in record:
            # Source confidence belongs to an occurrence, not an equal word.
            # Without a uniquely owned geometric block and one matching token
            # inside it, retain the record for the caller's unplaced disclosure.
            bounds = (source_ranges or {}).get(index)
            if bounds is None:
                continue
            occurrences = [raw for node in complete_nodes for _local, raw in node.matches(token)
                           if bounds[0] <= raw[0] and raw[1] <= bounds[1]]
            if len(occurrences) != 1:
                continue
            matches = [(node, local, raw) for node in nodes
                       for local, raw in node.matches(token)
                       if bounds[0] <= raw[0] and raw[1] <= bounds[1]]
            if len(matches) != 1:
                continue
            node, local, found = matches[0]
            node.taken.append(local)
        else:
            found = next((candidate for node in nodes
                          if (candidate := node.find(token)) is not None), None)
        if found is not None:
            edits.append((found[0], found[1], _title(readings)))
            # Return the caller's actual record, preserving placement identity.
            placed.append(span)

    if not edits:
        return html, []
    out = html
    for start, end, title in sorted(edits, key=lambda e: e[0], reverse=True):
        out = "%s<span class=\"%s\" title=\"%s\">%s</span>%s" % (
            out[:start], CLASS, _attr(title), out[start:end], out[end:])
    return out, placed


def is_recovered_marker(span, recovered):
    """True when a flagged reading is a note marker restored by the gate.

    A resolved marker must not also be reported as unresolved prose."""
    if not recovered:
        return False
    numbers = {int(n) for n in recovered}
    _token, readings = uncertain_readings(span)
    return any(int(run) in numbers
               for reading in readings for run in _DIGITS.findall(reading))


def annotate_page(html, spans, recovered=(), with_placements=False, *, source_ranges=None):
    """The whole of R3 for one page. Returns ``(html, uncertain, marked)``.

    ``uncertain`` is what the reader should still be told about: everything that
    got a mark, plus anything that could not be placed and is not explained by a
    marker recovery. Order is preserved so the about page lists a page's readings
    in the order the model reported them.
    """
    spans = [record for record in (uncertain_record(s) for s in spans or ())
             if record is not None]
    if not spans:
        return (html, [], 0, []) if with_placements else (html, [], 0)
    html, placed = mark_uncertain(html, spans, source_ranges=source_ranges)
    marked = [id(record) for record in placed]
    uncertain = [record for record in spans
                 if id(record) in marked or not is_recovered_marker(record, recovered)]
    result = (html, uncertain, len(placed))
    if with_placements:
        return (*result, [i for i, record in enumerate(uncertain) if id(record) in marked])
    return result
