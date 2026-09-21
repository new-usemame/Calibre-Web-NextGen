"""Canonical, inactive source enrichment for wrapper operations.

Recovery is rendered exactly once with the existing annotator. Wrappers move
that tree; they never place confidence marks again against changed text nodes.
"""
import copy
import json
from dataclasses import dataclass, replace, asdict
from xml.dom import Node, minidom

from . import annotate

VERSION = 'reflow-enriched-source-3'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


@dataclass(frozen=True)
class SourcePage:
    page: int
    state_digest: str
    html: str
    blocks_json: str
    provenance_json: str
    records_json: str
    report_json: str
    seal: str = ""

    @property
    def identity(self):
        from .structural_ops import _digest
        provenance = json.loads(self.provenance_json)
        # Cache timing/hit observations remain in the sealed current report but
        # do not change source meaning, confidence, raster or request identity.
        for key in ('seconds', 'reused'): provenance.pop(key, None)
        return _digest([VERSION, self.page, self.state_digest, self.html,
                        self.blocks_json, provenance, self.records_json, self.report_json])

    def _identity(self):
        from .structural_ops import _digest
        return _digest([VERSION, self.page, self.state_digest, self.html,
                        self.blocks_json, self.provenance_json, self.records_json, self.report_json])

    def validate(self, book):
        from .structural_ops import _digest, _state, ContractError
        if self._identity() != self.seal:
            raise ContractError('canonical source bytes or records changed')
        if _digest(_state(book, self.page)) != self.state_digest:
            raise ContractError('stale canonical source page')

    def report(self):
        return json.loads(self.report_json)

    def render(self, book, wrappers):
        from . import build_epub
        self.validate(book)
        blocks = build_epub.split_blocks(self.html)
        mapping = json.loads(self.blocks_json)
        for index, specs in wrappers.items():
            block_index = mapping[str(index)]
            blocks[block_index] = wrap(blocks[block_index], book.pages[self.page][index], specs)
        return '\n'.join(blocks)


def _image_owned_records(book,pno,provenance,records):
    """Do not relocate image/caption confidence onto equal surviving tokens."""
    from . import extract
    from .heading_evidence import _valid, _inside
    if provenance.get('layer')!='ocr':return [],[]
    angle=provenance.get('orientation',0)
    if angle not in (0,90,180,270):return [],[]
    frame=provenance.get('page_rect')
    if angle and not _valid(frame):return [],[]
    matrix=extract.pymupdf.Matrix(1,1).prerotate(angle)
    origin=extract.pymupdf.Rect(frame)*matrix if frame else extract.pymupdf.Rect(0,0,0,0)
    figures=[f for f in book.figures if f['pno']==pno and
             f.get('source_geometry',{}).get('space')=='reading']
    artwork=[a['bbox'] for a in book.artwork if a['pno']==pno and _valid(a['bbox'])
             and any(_inside(a['bbox'],f['bbox']) for f in figures)]
    elements=book.pages[pno]
    art,qualified=[],[]
    for index,record in enumerate(records):
        box=record.get('source_bbox')
        if not _valid(box):continue
        mapped=extract.pymupdf.Rect(box)*matrix
        mapped=(mapped.x0-origin.x0,mapped.y0-origin.y0,mapped.x1-origin.x0,mapped.y1-origin.y0)
        owners=[e for e in elements if e.kind in ('p','h','caption') and
                _valid(e.bbox) and _inside(mapped,e.bbox)]
        if any(e.kind=='caption' and e.caption_uncertain for e in owners):
            qualified.append(index)
        elif not owners and any(_inside(mapped,box) for box in artwork):
            art.append(index)
    return art,qualified


def prepare_source_page(book, pno, provenance, records=()):
    """Capture actual Recovery provenance/records; callers regenerate on replay.

    Does not activate the pipeline. Callers must pass this current SourcePage to
    preparation, admission and final build, including deterministic fallback.
    """
    from . import build_epub
    from .structural_ops import _digest, _state, ContractError
    if pno not in book.pages or not isinstance(provenance, dict):
        raise ContractError('source page and Recovery provenance are required')
    if provenance.get('failed'):
        raise ContractError('failed Recovery source is not supported')
    raw = copy.deepcopy(list(records))
    if ('uncertain_words' in provenance and provenance['uncertain_words'] != len(raw)) or ('uncertain' in provenance and _json(provenance['uncertain']) != _json(raw)):
        raise ContractError('Recovery uncertainty records are missing or incomplete')
    mapping = {}
    html = build_epub.page_fragment(book, pno, element_blocks=mapping)
    normalized=[record for record in (annotate.uncertain_record(r) for r in raw) if record is not None]
    artwork,qualified=_image_owned_records(book,pno,provenance,normalized)
    excluded=set(artwork+qualified)
    active=[i for i in range(len(normalized)) if i not in excluded]
    html, _, marked, placements = annotate.annotate_page(html,[normalized[i] for i in active],with_placements=True)
    placed=[active[i] for i in placements]
    report = {'version': VERSION, 'records_sha256': _digest(raw), 'raw_records': len(raw),
              'normalized_raw_indices': [i for i, r in enumerate(raw) if annotate.uncertain_record(r) is not None],
              'record_ids': [_digest([i, record]) for i, record in enumerate(raw)],
              'uncertain': normalized, 'marked': marked, 'placed_record_indices': placed,
              'artwork_record_indices': artwork, 'qualified_caption_record_indices': qualified,
              'unplaced_record_indices': [i for i in range(len(normalized)) if i not in placed]}
    page = SourcePage(pno, _digest(_state(book, pno)), html, _json(mapping),
                      _json(provenance), _json(raw), _json(report))
    return replace(page, seal=page._identity())


def prepare_recovery_page(book, pno, recovery):
    """Use complete Recovery fields, including original-coordinate transforms."""
    provenance = asdict(recovery.provenance[pno])
    return prepare_source_page(book, pno, provenance, provenance['uncertain'])


def _tree(fragment, element):
    from .structural_ops import ContractError, _length
    try:
        root = minidom.parseString('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')
    except Exception as exc:
        raise ContractError('canonical source markup cannot be parsed') from exc
    nodes = [n for n in root.documentElement.childNodes if n.nodeType == Node.ELEMENT_NODE]
    if len(nodes) != 1 or nodes[0].tagName != 'p':
        raise ContractError('wrapper requires one canonical paragraph')
    marker_lengths = iter(len(str(r[1])) for r in element.runs if r[0] != 't')
    bounds, atoms, offset = {}, set(), 0

    def visit(node):
        nonlocal offset
        start = offset
        if node.nodeType == Node.TEXT_NODE:
            offset += len(node.data)
        elif node.nodeType == Node.ELEMENT_NODE:
            classes = node.getAttribute('class').split()
            marker = 'noteref' in classes or 'noteref-unresolved' in classes
            if marker:
                try: offset += next(marker_lengths)
                except StopIteration as exc: raise ContractError('source marker mapping differs') from exc
                atoms.add(id(node))
            else:
                for child in node.childNodes: visit(child)
                if annotate.CLASS in classes: atoms.add(id(node))
        bounds[id(node)] = (start, offset)
    node = nodes[0];visit(node)
    if offset != _length(element.runs) or next(marker_lengths, None) is not None:
        raise ContractError('canonical paragraph offsets differ from immutable runs')
    return node, bounds, atoms


def wrap(fragment, element, specs):
    """Slice existing inline nodes at source offsets, never through mark atoms."""
    from .structural_ops import ContractError
    node, bounds, atoms = _tree(fragment, element)
    total = bounds[id(node)][1]
    specs = sorted(specs, key=lambda s: s.start)
    for spec in specs:
        for key in atoms:
            start, end = bounds[key]
            if start < spec.start < end or start < spec.end < end:
                raise ContractError('operation splits a canonical uncertainty or marker atom')
    if specs[0].kind == 'heading':
        out = node.cloneNode(True);out.tagName = 'h2'
        return out.toxml()

    def sliced(current, lo, hi):
        start, end = bounds[id(current)]
        if end <= lo or start >= hi: return None
        if id(current) in atoms or lo <= start and end <= hi:
            return current.cloneNode(True)
        if current.nodeType == Node.TEXT_NODE:
            return current.ownerDocument.createTextNode(current.data[max(0, lo-start):min(end-start, hi-start)])
        out = current.cloneNode(False)
        for child in current.childNodes:
            result = sliced(child, lo, hi)
            if result is not None: out.appendChild(result)
        return out
    pieces, cursor = [], 0
    for spec in specs:
        if cursor < spec.start: pieces.append(sliced(node, cursor, spec.start).toxml())
        pieces.append('<blockquote>' + sliced(node, spec.start, spec.end).toxml() + '</blockquote>')
        cursor = spec.end
    if cursor < total: pieces.append(sliced(node, cursor, total).toxml())
    return '\n'.join(pieces)
