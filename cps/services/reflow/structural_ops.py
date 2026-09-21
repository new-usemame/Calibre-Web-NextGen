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

from . import extract, heading_evidence

PROTOCOL = "cwng-source-wrappers-v1"
MAX_CANDIDATES = 64
MAX_CONTEXT_CHARS = 24000
PROVENANCE_KEYS = {"layer", "reason", "engine", "language", "language_identity",
                   "requested_dpi", "effective_dpi", "orientation", "orientation_confidence",
                   "flags", "words", "uncertain_words", "failed", "pno"}


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
    source_page: object = None
    heading_source_json: str = ""
    heading_source_digest: str = ""

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

    def accept(self, book, doc, response, source_page=None):
        bound = getattr(self, "source_page", None)
        if bound is not None:
            if source_page is None or source_page.identity != bound.identity:
                raise ContractError("current enriched source identity is required")
            source_page.validate(book)
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
        headings = [spec for spec in picked if spec.kind == 'heading']
        if headings:
            try:
                geometry = json.loads(self.heading_source_json)
                if (_digest(geometry) != self.heading_source_digest or
                        geometry['version'] != heading_evidence.VERSION):
                    raise ValueError('stale geometry')
                proofs = heading_evidence.heading_evidence(book, self.page,
                    geometry['raw_page'], geometry['layer'], geometry['source_rotation'], geometry['reading_size'])
                if _digest(proofs) != geometry['proofs_digest'] or any(
                        not proofs[spec.element]['supported'] for spec in headings):
                    raise ValueError('unsupported current source role')
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ContractError('current source heading evidence is required') from exc
        for left, right in zip(picked, picked[1:]):
            if left.element == right.element and left.end > right.start:
                raise ContractError("overlapping selections")
        return OperationPlan(self, tuple(selected))


@dataclass(frozen=True)
class OperationPlan:
    prepared: Prepared
    selected: tuple

    def compile(self, book, doc, source_page=None):
        # Re-admit at the final publication seam, including source/inventory and
        # uncertainty metadata. A previously accepted plan can become stale.
        self.prepared.accept(book, doc, {"protocol": PROTOCOL,
            "snapshot_id": self.prepared.snapshot_id, "select": list(self.selected)}, source_page=source_page)
        by_id = {row["candidate_id"]: spec
                 for row, spec in zip(self.prepared.candidates(), self.prepared.specs)}
        result = {}
        for cid in self.selected:
            spec = by_id[cid]
            result.setdefault(spec.element, []).append(spec)
        return result


def prepare(book, doc, pno, revision, source_layer, seed=0,
            max_candidates=MAX_CANDIDATES, max_context_chars=MAX_CONTEXT_CHARS, source_page=None,
            raw_page=None):
    if pno not in book.pages or not revision or not isinstance(source_layer, dict):
        raise ContractError("source page, revision and provenance are required")
    if not 1 <= max_candidates <= MAX_CANDIDATES or not 1 <= max_context_chars <= MAX_CONTEXT_CHARS:
        raise ContractError("invalid preparation bounds")
    # Word-level OCR confidence lives outside Book. Until this seam can bind
    # and render those records, this is unsupported context, not an abstention.
    if source_layer.get("failed") or (source_page is None and (source_layer.get("uncertain_words") or source_layer.get("uncertain"))):
        raise ContractError("source word uncertainty is not supported by wrapper operations")
    if source_page is not None:
        from .enriched_source import SourcePage
        if not isinstance(source_page, SourcePage) or source_page.page != pno:
            raise ContractError('canonical source page required')
        source_page.validate(book)
        if _digest(json.loads(source_page.provenance_json)) != _digest(source_layer):
            raise ContractError('current Recovery provenance differs')
    source_layer = {k: copy.deepcopy(v) for k, v in source_layer.items()
                    if k in PROVENANCE_KEYS}
    if len(json.dumps(source_layer)) > 4096:
        raise ContractError("source provenance exceeds preparation bound")
    pdf_digest = _pdf_digest(doc)
    state_digest = _digest(_state(book, pno))
    from .source_display import grid_regions
    relational_regions = grid_regions(book, doc, pno, source_layer)
    # Retain the already-extracted source, including Recovery geometry. Never
    # recreate native spans to stand in for OCR provenance or missing context.
    raw = raw_page.to_dict() if hasattr(raw_page, 'to_dict') else raw_page
    raw = json.loads(json.dumps(raw))
    from .source_display import SourceDisplay
    reading_rect = SourceDisplay(doc, pno, source_layer).rect
    reading_size = [reading_rect.width, reading_rect.height]
    proofs = heading_evidence.heading_evidence(book, pno, raw, source_layer.get('layer'),
                                              doc[pno].rotation, reading_size)
    geometry = {'version': heading_evidence.VERSION, 'raw_page': raw,
                'layer': source_layer.get('layer'), 'source_rotation': doc[pno].rotation,
                'reading_size': reading_size,
                'proofs_digest': _digest(proofs)}
    geometry_digest = _digest(geometry)
    context, omitted, specs, used = [], [], [], 0
    for index, element in enumerate(book.pages[pno]):
        record = {"id": "e%d" % index, **asdict(element)}
        record['heading_evidence'] = proofs[index]
        if source_page is not None:
            from .build_epub import split_blocks
            mapping = json.loads(source_page.blocks_json)
            if str(index) in mapping:
                record['canonical_xhtml'] = split_blocks(source_page.html)[mapping[str(index)]]
        size = len(json.dumps(record))
        if used + size > max_context_chars:
            omitted.append("e%d" % index)
            continue
        used += size
        context.append(record)
        for spec in (() if index in relational_regions else _proposals(element, index)):
            if spec.kind == 'heading' and not proofs[index]['supported']:
                continue
            if source_page is not None:
                from .enriched_source import wrap
                try: wrap(record['canonical_xhtml'], element, [spec])
                except ContractError: continue
            specs.append(spec)
    if source_layer.get('layer') == 'ocr':
        from .source_display import SourceDisplay
        raster = SourceDisplay(doc, pno, source_layer).jpeg(scale=1.5, quality=85)
    else:
        raster = extract.render_page_jpeg(doc, pno, scale=1.5, quality=85,
                                          max_bytes=2 * 1024 * 1024)
    snapshot_id = _digest([PROTOCOL, revision, pdf_digest, pno, state_digest,
                           hashlib.sha256(raster).hexdigest(), source_layer])
    if source_page is not None:
        snapshot_id = _digest([snapshot_id, source_page.identity])
    if relational_regions:
        snapshot_id = _digest([snapshot_id, relational_regions])
    snapshot_id = _digest([snapshot_id, geometry_digest])
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
    coverage["source_grid_elements"] = ["e%d" % i for i in relational_regions]
    coverage['heading_evidence_version'] = heading_evidence.VERSION
    coverage['heading_supported_elements'] = ['e%d' % i for i, proof in proofs.items()
        if proof['supported'] and i not in relational_regions and book.pages[pno][i].kind == 'p']
    coverage['heading_unsupported_elements'] = ['e%d' % i for i, proof in proofs.items()
        if not proof['supported'] and book.pages[pno][i].kind == 'p']
    coverage["supplied_book_scope"] = ("full_book" if set(book.pages) == set(range(len(doc)))
                                      else "selected_pages")
    coverage["available_book_pages"] = len(book.pages)
    coverage["model_context_scope"] = "current_page"
    state = {"elements": context, "notes": notes, "figures": figures,
             "source_layer": source_layer, "page_rect": list(doc[pno].rect),
             "page_rotation": doc[pno].rotation,
             "heading_source_sha256": geometry_digest,
             "source_context": "current page; immutable inventories bound to supplied Book"}
    if source_page is not None:
        report = source_page.report()
        state['source_enrichment'] = {'version': report['version'], 'identity': source_page.identity,
            'records_sha256': report['records_sha256'], 'raw_records': report['raw_records'],
            'marked': report['marked'], 'unplaced': [report['uncertain'][i]
                for i in report['unplaced_record_indices']]}
        if used + len(json.dumps(state['source_enrichment'])) > max_context_chars:
            raise ContractError('enriched confidence context exceeds preparation bound')
    return Prepared(pno, revision, state_digest, pdf_digest, snapshot_id, tuple(specs),
                    json.dumps(state), raster, seed, json.dumps(coverage), source_page,
                    json.dumps(geometry), geometry_digest)


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


VERIFICATION_PROTOCOL = "cwng-source-wrapper-approval-v1"


def proposal_identity(snapshot_id, proposed_ids):
    """Bind an order-independent proposal set to its immutable source snapshot."""
    return _digest([VERIFICATION_PROTOCOL, snapshot_id, sorted(proposed_ids)])


@dataclass(frozen=True)
class VerificationDecision:
    """A failed verifier never supplies the proposer plan as a fallback."""
    plan: object = None
    rejected: bool = False
    reason: str = ""

    @property
    def operation_plans(self):
        return () if self.plan is None else (self.plan,)


@dataclass(frozen=True)
class Verification:
    proposal: OperationPlan

    @property
    def proposal_id(self):
        return proposal_identity(self.proposal.prepared.snapshot_id, self.proposal.selected)

    def model_view(self):
        view = self.proposal.prepared.model_view()
        view['verification'] = {'protocol': VERIFICATION_PROTOCOL,
            'proposal_id': self.proposal_id, 'proposed_ids': sorted(self.proposal.selected)}
        return view

    def empty_response(self):
        return {'protocol': VERIFICATION_PROTOCOL,
                'snapshot_id': self.proposal.prepared.snapshot_id,
                'proposal_id': self.proposal_id, 'approve': []}

    def accept(self, book, doc, response, source_page=None):
        """Admit only an approved subset; protocol rejection is atomic.

        Semantic correctness is still a model judgment, not proven by this guard.
        The returned plan retains the existing final-builder stale-source check.
        """
        self.proposal.compile(book, doc, source_page=source_page)
        expected = self.empty_response()
        if not isinstance(response, dict) or set(response) != set(expected):
            raise ContractError('unsupported verification response fields')
        if any(response[k] != expected[k] for k in expected if k != 'approve'):
            raise ContractError('stale verification protocol, snapshot or proposal')
        approved = response['approve']
        if not isinstance(approved, list) or any(not isinstance(cid, str) for cid in approved):
            raise ContractError('invalid approval list')
        if any(cid not in self.proposal.selected for cid in approved):
            raise ContractError('approval contains an unproposed candidate')
        p = self.proposal.prepared
        return p.accept(book, doc, {'protocol': PROTOCOL,
            'snapshot_id': p.snapshot_id, 'select': approved}, source_page=source_page)

    def resolve(self, book, doc, response, source_page=None):
        """Explicit deterministic fallback for a rejected/unparseable response.

        Transport failure supplies no response. Billing and transport handling
        remain caller responsibilities; this pure seam does not make requests.
        """
        try:
            return VerificationDecision(plan=self.accept(book, doc, response, source_page=source_page))
        except ContractError as exc:
            return VerificationDecision(rejected=True, reason=str(exc))


def prepare_verification(book, doc, proposal, source_page=None):
    if not isinstance(proposal, OperationPlan):
        raise ContractError('an admitted operation proposal is required')
    proposal.compile(book, doc, source_page=source_page)
    return Verification(proposal)
