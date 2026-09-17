# SPDX-License-Identifier: GPL-3.0-or-later
"""Inactive, source-bound wrapper operations; no provider or task activation.

Legality is not semantic correctness. Uniform proposals include wrong choices;
source judgments belong to an independent oracle, never to this adapter.
"""
import base64
import copy
import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass

from . import extract

PROTOCOL = "cwng-source-wrappers-v1"
MAX_CANDIDATES = 64
MAX_CONTEXT_CHARS = 24000
PROVENANCE_KEYS = {"layer", "reason", "engine", "language", "language_identity",
                   "requested_dpi", "effective_dpi", "orientation", "orientation_confidence",
                   "flags", "words", "uncertain_words", "reused", "failed", "pno"}


class ContractError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _state(book, pno):
    neighbors = {str(n): [asdict(e) for e in book.pages.get(n, [])]
                 for n in (pno - 1, pno, pno + 1)}
    return {"pages": neighbors, "notes": [asdict(n) for n in book.notes],
            "figures": book.figures, "artwork": book.artwork,
            "uncertain_notes": sorted(book.ambiguous_note_numbers(pno))}


def _pdf_digest(doc):
    # Unnamed documents serialize with changing PDF identifiers. Do not invent
    # a stable source identity for that unsupported adapter input.
    if doc is None or not doc.name:
        raise ContractError("a named immutable source PDF is required")
    return extract.document_fingerprint(doc)


def _length(runs):
    return sum(len(str(run[1])) for run in runs)


def _slice(runs, start, end):
    result, offset = [], 0
    for run in runs:
        text = str(run[1])
        lo, hi = max(0, start - offset), min(len(text), end - offset)
        if lo < hi:
            if run[0] != "t" and (lo or hi != len(text)):
                raise ContractError("an atomic source marker cannot be split")
            result.append(copy.deepcopy(run) if run[0] != "t" else
                          [run[0], text[lo:hi], *copy.deepcopy(run[2:])])
        offset += len(text)
    return result


@dataclass(frozen=True)
class _Spec:
    element: int
    kind: str
    start: int
    end: int


def _proposals(element, index):
    """Boundaries are generic source punctuation, never known-answer strings."""
    if element.kind != "p" or element.table_row or not element.text.strip():
        return []
    text = "".join(str(run[1]) for run in element.runs)
    end = _length(element.runs)
    specs = [_Spec(index, "quote", 0, end)]
    if len(text.split()) <= 40:
        specs.append(_Spec(index, "heading", 0, end))
    # Full paragraph, prefix, and quoted substring are distinct legal choices.
    # A prefix may include an attribution; source pixels decide whether it is
    # part of the printed quotation. A complete paragraph can be overlong.
    for match in re.finditer(r'[.!?][”\"]?(?=\s|$)|[”\"]', text):
        boundary = match.end()
        # A source marker immediately after punctuation belongs to that boundary
        # as an indivisible atom; never move it to the following sentence.
        offset = 0
        for run in element.runs:
            size = len(str(run[1]))
            if offset == boundary and run[0] != "t":
                boundary += size
            offset += size
        if 0 < boundary < end:
            specs.append(_Spec(index, "quote", 0, boundary))
    for match in re.finditer(r'“[^”]+”|"[^"\n]+"', text):
        if match.start() or match.end() != end:
            specs.append(_Spec(index, "quote", match.start(), match.end()))
    # Do not admit a boundary through a marker or emit duplicate choices.
    legal = []
    for spec in dict.fromkeys(specs):
        try:
            _slice(element.runs, spec.start, spec.end)
        except ContractError:
            continue
        legal.append(spec)
    return legal


@dataclass(frozen=True)
class Prepared:
    page: int
    revision: str
    state_digest: str
    pdf_digest: str
    snapshot_id: str
    specs: tuple
    context_json: str
    raster: bytes
    seed: int
    coverage_json: str

    def candidates(self):
        rows = []
        for spec in self.specs:
            cid = "op-" + _digest([self.snapshot_id, asdict(spec)])[:24]
            rows.append({"candidate_id": cid, "kind": spec.kind,
                         "element_id": "e%d" % spec.element,
                         "source_range": [spec.start, spec.end],
                         "current_state": "paragraph",
                         "proposed_state": "h2" if spec.kind == "heading" else "blockquote"})
        return rows

    def model_view(self):
        return {"protocol": PROTOCOL, "snapshot_id": self.snapshot_id,
                "page_index0": self.page, "source_revision": self.revision,
                "source_pdf_sha256": self.pdf_digest,
                "context": json.loads(self.context_json),
                "coverage": json.loads(self.coverage_json),
                "candidates": self.candidates(),
                "source_image": {"sha256": hashlib.sha256(self.raster).hexdigest(),
                    "data_url": "data:image/jpeg;base64," + base64.b64encode(self.raster).decode()}}

    def accept(self, book, doc, response):
        if _digest(_state(book, self.page)) != self.state_digest or _pdf_digest(doc) != self.pdf_digest:
            raise ContractError("stale source or current state")
        if not isinstance(response, dict) or set(response) != {"protocol", "snapshot_id", "select"}:
            raise ContractError("unsupported response fields; prose and edits are forbidden")
        if response["protocol"] != PROTOCOL or response["snapshot_id"] != self.snapshot_id:
            raise ContractError("stale protocol or snapshot")
        selected = response["select"]
        if not isinstance(selected, list) or len(selected) > len(self.specs) \
                or any(not isinstance(cid, str) for cid in selected):
            raise ContractError("invalid selection list")
        if len(set(selected)) != len(selected):
            raise ContractError("duplicate selection")
        by_id = {row["candidate_id"]: spec for row, spec in zip(self.candidates(), self.specs)}
        if any(cid not in by_id for cid in selected):
            raise ContractError("unknown or unsupported candidate")
        picked = sorted((by_id[cid] for cid in selected), key=lambda s: (s.element, s.start, s.end))
        for left, right in zip(picked, picked[1:]):
            if left.element == right.element and left.end > right.start:
                raise ContractError("overlapping selections")
        return OperationPlan(self, tuple(selected))


@dataclass(frozen=True)
class OperationPlan:
    prepared: Prepared
    selected: tuple

    def compile(self, book, doc):
        # Re-admit at the final publication seam, including source/inventory and
        # uncertainty metadata. A previously accepted plan can become stale.
        self.prepared.accept(book, doc, {"protocol": PROTOCOL,
            "snapshot_id": self.prepared.snapshot_id, "select": list(self.selected)})
        by_id = {row["candidate_id"]: spec
                 for row, spec in zip(self.prepared.candidates(), self.prepared.specs)}
        result = {}
        for cid in self.selected:
            spec = by_id[cid]
            result.setdefault(spec.element, []).append(spec)
        return result


def prepare(book, doc, pno, revision, source_layer, seed=0,
            max_candidates=MAX_CANDIDATES, max_context_chars=MAX_CONTEXT_CHARS):
    if pno not in book.pages or not revision or not isinstance(source_layer, dict):
        raise ContractError("source page, revision and provenance are required")
    if not 1 <= max_candidates <= MAX_CANDIDATES or not 1 <= max_context_chars <= MAX_CONTEXT_CHARS:
        raise ContractError("invalid preparation bounds")
    source_layer = {k: copy.deepcopy(v) for k, v in source_layer.items()
                    if k in PROVENANCE_KEYS}
    if len(json.dumps(source_layer)) > 4096:
        raise ContractError("source provenance exceeds preparation bound")
    pdf_digest = _pdf_digest(doc)
    state_digest = _digest(_state(book, pno))
    context, omitted, specs, used = [], [], [], 0
    for index, element in enumerate(book.pages[pno]):
        record = {"id": "e%d" % index, **asdict(element)}
        size = len(json.dumps(record))
        if used + size > max_context_chars:
            omitted.append("e%d" % index)
            continue
        used += size
        context.append(record)
        specs.extend(_proposals(element, index))
    raster = extract.render_page_jpeg(doc, pno, scale=1.5, quality=85,
                                      max_bytes=2 * 1024 * 1024)
    snapshot_id = _digest([PROTOCOL, revision, pdf_digest, pno, state_digest,
                           hashlib.sha256(raster).hexdigest(), source_layer])
    total = len(specs)
    random.Random(seed).shuffle(specs)
    specs = specs[:max_candidates]
    coverage = {"page_elements_total": len(book.pages[pno]),
                "page_elements_sent": len(context), "omitted_element_ids": omitted,
                "legal_choices_before_limit": total, "legal_choices_sent": len(specs),
                "candidate_limit_applied": total > len(specs), "presentation_seed": seed,
                "unsupported_operation_kinds": ["note_attachment", "figure_attachment", "table_layout"]}
    notes, figures = [], []
    omitted_inventory = 0
    for group, records in ((notes, [asdict(n) for n in book.notes if n.pno == pno]),
                           (figures, [f for f in book.figures if f["pno"] == pno])):
        for record in records:
            size = len(json.dumps(record))
            if used + size > max_context_chars:
                omitted_inventory += 1
                continue
            used += size
            group.append(record)
    coverage["omitted_inventory_records"] = omitted_inventory
    coverage["supplied_book_scope"] = ("full_book" if set(book.pages) == set(range(len(doc)))
                                      else "selected_pages")
    coverage["available_book_pages"] = len(book.pages)
    coverage["model_context_scope"] = "current_page"
    state = {"elements": context, "notes": notes, "figures": figures,
             "source_layer": source_layer, "page_rect": list(doc[pno].rect),
             "page_rotation": doc[pno].rotation,
             "source_context": "current page; immutable inventories bound to supplied Book"}
    return Prepared(pno, revision, state_digest, pdf_digest, snapshot_id, tuple(specs),
                    json.dumps(state), raster, seed, json.dumps(coverage))


def render_element(element, specs, render_runs):
    """Only wrappers change. Source run types, metadata and order survive slicing."""
    specs = sorted(specs, key=lambda s: s.start)
    if specs[0].kind == "heading":
        return "<h2>%s</h2>" % render_runs(element.runs)
    out, cursor = [], 0
    for spec in specs:
        before = _slice(element.runs, cursor, spec.start)
        if any(str(r[1]).strip() for r in before):
            out.append("<p>%s</p>" % render_runs(before))
        out.append("<blockquote><p>%s</p></blockquote>" % render_runs(
            _slice(element.runs, spec.start, spec.end)))
        cursor = spec.end
    tail = _slice(element.runs, cursor, _length(element.runs))
    if any(str(r[1]).strip() for r in tail):
        out.append("<p>%s</p>" % render_runs(tail))
    return "\n".join(out)
