"""Preserve unmatched recognition ink in existing, mandatory source figures.

This never adopts recognition text or clears the raw transcript uncertainty.
Only a complete current geometry proof permits a speculative native skeleton.
"""
import copy
import math
import re
import os
from dataclasses import replace

from . import extract, skeleton, transcript
from .structural_ops import ContractError

VERSION = 'native-unmatched-source-regions-2'
_CROPS = {'scan_figure_band', 'scan_figure_side', 'native_spacing_uncertain'}


def _box(value):
    return (type(value) in (list, tuple) and len(value) == 4
            and all(type(n) in (int, float) and math.isfinite(n) for n in value)
            and value[0] < value[2] and value[1] < value[3])


def _inside(a, b):
    return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3]


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _single_spacing_owner(measured):
    """Merge overlapping source crops without losing either crop's pixels.

    An uncertain heading may already have its own crop when diagram recovery
    extends into it. A bounded union preserves both original crops. Refuse if
    that union would repeat any other reading region or image. Artwork ledger
    entries already wholly owned by either crop remain untouched.
    """
    for title in list(measured.regions):
        if title.kind != 'figure' or title.reason != 'native_spacing_uncertain':
            continue
        carriers = [r for r in measured.regions if r is not title and r.kind == 'figure'
                    and r.reason in ('scan_figure_band', 'scan_figure_side')
                    and _overlap(title.bbox, r.bbox)]
        if not carriers:
            continue
        if len(carriers) != 1 or title.lines or title.caption_lines or title.list_groups:
            return False
        carrier = carriers[0]
        a, b = title.bbox, carrier.bbox
        union = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
        for other in measured.regions:
            if other is title or other is carrier or not _overlap(other.bbox, union):
                continue
            if other.kind == 'artwork' and (_inside(other.bbox, a) or _inside(other.bbox, b)):
                continue
            return False
        carrier.bbox = union
        # Keep the existing spacing disclosure and any diagram caption. Neither
        # the uncertain heading nor the diagram receives inferred source words.
        carrier.reason = 'native_spacing_uncertain'
        measured.regions.remove(title)
    return True


def candidate(raw, provenance, doc, style, *, pixel_probe, layer_trusted):
    """Return an isolated full skeleton and complete proof, or ordinary refusal."""
    v = provenance.get('verification', {})
    if type(v) is not dict:
        return None
    if (getattr(doc, 'is_dirty', True) or not getattr(doc, 'name', '') or not os.path.isfile(doc.name)
            or not raw.transcript_unverified or provenance.get('layer') != 'native'
            or provenance.get('reason') != 'verify_scan_transcript' or provenance.get('failed')
            or 'native_transcript_corroborated' not in provenance.get('flags', ())
            or v.get('version') != transcript.VERSION or v.get('page') != raw.pno
            or v.get('source_pdf_sha256') != extract.document_fingerprint(doc)
            or not re.fullmatch(r'[0-9a-f]{64}', str(v.get('recognition_sha256', '')))
            or v.get('recognition_orientation') != 0 or doc[raw.pno].rotation != 0
            or raw.source_geometry or tuple(doc[raw.pno].rect) != (0, 0, raw.width, raw.height)):
        return None
    boxes = v.get('unmatched_pdf_boxes')
    count = v.get('unmatched_words')
    frame = (0, 0, raw.width, raw.height)
    if (type(count) is not int or not 0 < count <= 10000 or type(boxes) is not list
            or len(boxes) != count or any(not _box(b) or not _inside(b, frame) for b in boxes)):
        return None
    lines = [l for b in raw.text_blocks for l in b.lines]
    if any(not _box(l.bbox) or any(_overlap(b, l.bbox) for b in boxes) for l in lines):
        return None
    isolated = replace(copy.deepcopy(raw), transcript_unverified=False)
    lineage = {id(clone): original for a, b in zip(raw.blocks, isolated.blocks)
               for original, clone in zip(a.lines, b.lines)}
    measured = skeleton.page_skeleton(isolated, style, pixel_probe=pixel_probe, layer_trusted=layer_trusted)
    if not _single_spacing_owner(measured):
        return None
    owners = []
    for box in boxes:
        matches = [r for r in measured.regions if r.kind == 'figure' and r.reason in _CROPS
                   and _box(r.bbox) and _inside(r.bbox, frame) and _inside(box, r.bbox)]
        if len(matches) != 1:
            return None
        owner = matches[0]
        owners.append(dict(bbox=list(owner.bbox), reason=owner.reason))
    # Restore exact occurrence identity only for unchanged descendants of this
    # particular deep copy. No matching by equal words or guessed rectangles.
    # Newly created or further-qualified lines stay unbound and cannot later
    # become editable furniture through the raw source inventory.
    def original(line):
        parent = lineage.get(id(line))
        return parent if parent is not None and line == parent else line
    for region in measured.regions:
        region.lines = [original(line) for line in region.lines]
        region.caption_lines = [original(line) for line in region.caption_lines]
        region.list_groups = [[original(line) for line in group] for group in region.list_groups]
    proof = dict(version=VERSION, page=raw.pno, source_pdf_sha256=v['source_pdf_sha256'],
                 recognition_sha256=v['recognition_sha256'], frame=list(frame),
                 unmatched_pdf_boxes=copy.deepcopy(boxes), owners=owners)
    return measured, proof


def _required(book, pno, proof):
    """Rebind the complete obligation, including owners removed after assembly."""
    def require(ok):
        if not ok:
            raise ContractError('source region coverage is incomplete or stale')
    require(type(proof) is dict and proof.get('version') == VERSION
            and proof.get('page') == pno and proof.get('source_pdf_sha256') == book.source_fingerprint
            and bool(re.fullmatch(r'[0-9a-f]{64}', str(proof.get('recognition_sha256', '')))))
    boxes, owners, frame = proof.get('unmatched_pdf_boxes'), proof.get('owners'), proof.get('frame')
    require(_box(frame) and frame[:2] == [0, 0] and type(boxes) is list and 0 < len(boxes) <= 10000
            and type(owners) is list and len(boxes) == len(owners))
    figures = [f for f in book.figures if f['pno'] == pno]
    required = {}
    for box, owner in zip(boxes, owners):
        require(_box(box) and _inside(box, frame) and type(owner) is dict
                and _box(owner.get('bbox')) and _inside(owner['bbox'], frame)
                and _inside(box, owner['bbox']) and owner.get('reason') in _CROPS)
        matches = [(i, f) for i, f in enumerate(figures)
                   if list(f['bbox']) == owner['bbox'] and f.get('found') == owner['reason']
                   and not f.get('source_geometry')]
        require(len(matches) == 1)
        index, figure = matches[0]
        required[f'images/fig_p{pno:04d}_{index}.jpg'] = figure
    return required


def bind(book, pno, proof):
    _required(book, pno, proof)
    if not hasattr(book, 'source_region_protection'):
        book.source_region_protection = {}
    book.source_region_protection[pno] = copy.deepcopy(proof)


def verify_source(book, pno, provenance, html):
    from .build_epub import _IMG_SRC
    proof = getattr(book, 'source_region_protection', {}).get(pno)
    disposition = provenance.get('verification', {}).get('region_disposition')
    if proof is None and disposition is None:
        return False
    if proof is None:
        raise ContractError('source region provenance lacks its book obligation')
    required = _required(book, pno, proof)
    v = provenance.get('verification', {})
    if (proof != disposition or v.get('unmatched_pdf_boxes') != proof.get('unmatched_pdf_boxes')
            or v.get('unmatched_words') != len(proof['unmatched_pdf_boxes'])
            or v.get('recognition_sha256') != proof['recognition_sha256']):
        raise ContractError('source region provenance does not bind complete coverage')
    if not set(required) <= set(_IMG_SRC.findall(html)):
        raise ContractError('source region image absent from canonical page')
    return True


def required_images(book, pages, doc):
    from . import visual_coverage
    required = visual_coverage.required_images(book, pages, doc)
    for pno, proof in getattr(book, 'source_region_protection', {}).items():
        if pno not in pages:
            continue
        if (doc is None or getattr(doc, 'is_dirty', True) or not getattr(doc, 'name', '')
                or not os.path.isfile(doc.name) or doc[pno].rotation != 0 or list(doc[pno].rect) != proof.get('frame')
                or extract.document_fingerprint(doc) != proof.get('source_pdf_sha256')):
            raise ContractError('source region publication frame changed')
        required.update(_required(book, pno, proof))
    return required
