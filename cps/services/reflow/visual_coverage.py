"""Opt-in complete source artwork coverage, before canonical source sealing.

Detector geometry is only a proposal. The current source frame, original pixel
bounds and every overlapping raw occurrence must independently admit the union.
"""
import hashlib
import json
import math
import weakref
from dataclasses import asdict, dataclass

from . import extract
from .visual_objects import MODEL_ID, MODEL_REVISION, MIN_CONFIDENCE, _digest, _json, _rect

VERSION = 'local-visual-coverage-1'
REGION_VERSION = 'local-visual-regions-1'
RASTER_VERSION = 'source-display-jpeg-1.5-q85-1'
_issued = weakref.WeakKeyDictionary()


def _need(condition, reason):
    if not condition:
        raise ValueError('visual coverage: ' + reason)


def _overlap(a, b):
    # Shared borders contain no new pixel area and must not absorb their text.
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


def render_input(doc, raw, *, version=VERSION):
    """Return the exact supported reading-frame raster and its complete binding."""
    from .model import _jpeg_dimensions
    from .source_display import SourceDisplay
    _need(bool(doc.name), 'named source PDF required')
    _need(version in (VERSION, REGION_VERSION), 'unsupported coverage version')
    _need(version != REGION_VERSION or not doc.is_dirty, 'uncommitted table source PDF')
    page = doc[raw.pno]
    frame = list(page.rect)
    geometry = dict(space='reading', orientation=0, source_rotation=page.rotation, page_rect=frame)
    _need(list(page.mediabox) == list(page.cropbox) and
          list(page.mediabox)[:2] == [0, 0], 'unsupported cropped source frame')
    _need(page.rotation in (0, 90) and frame[:2] == [0, 0] and
          (_json(raw.source_geometry) == _json(geometry) or
           (version == REGION_VERSION and not raw.source_geometry and page.rotation == 0)) and
          raw.width == frame[2] and raw.height == frame[3], 'unsupported or stale frame')
    _need(math.ceil(raw.width*1.5)*math.ceil(raw.height*1.5) <= 4_000_000, 'raster pixel bound')
    quality = 78 if version == REGION_VERSION else 85
    raster = SourceDisplay(doc, raw.pno, dict(layer='ocr', **geometry)).jpeg(scale=1.5, quality=quality)
    size = list(_jpeg_dimensions(raster))
    _need(size == [math.ceil(raw.width*1.5), math.ceil(raw.height*1.5)] and
          len(raster) <= 2*1024*1024, 'raster size bound')
    binding = dict(version=version, raster_version=('source-display-jpeg-1.5-q78-1'
        if version == REGION_VERSION else RASTER_VERSION),
        model_id=MODEL_ID, model_revision=MODEL_REVISION, page=raw.pno,
        pdf_sha256=extract.document_fingerprint(doc), raw_sha256=_digest(asdict(raw)),
        image_sha256=hashlib.sha256(raster).hexdigest(), image_size=size,
        source_geometry=geometry, mediabox=list(page.mediabox), cropbox=list(page.cropbox),
        derotation=list(page.derotation_matrix))
    return raster, binding


@dataclass(frozen=True, eq=False)
class _Observation:
    page: int
    raw_digest: str
    entries: tuple
    captions: tuple
    version: str = VERSION

    def identity(self):
        return _digest(asdict(self))



def _table_pixel_bounds(display, box):
    """A detector edge must terminate in source whitespace, not clipped ink.

    Search only eight reading points, using actual PDF pixels at 2x. This is
    geometry evidence, not a claim that a detector recognized every table cell.
    A crop with no closed two-pixel light border stays unsupported.
    """
    for margin in range(2, 9):
        expanded = (box[0]-margin, box[1]-margin, box[2]+margin, box[3]+margin)
        if not _contains(tuple(display.rect), expanded):
            continue
        pix = display.pixmap(expanded, scale=2, gray=True)
        w, h, data = pix.width, pix.height, pix.samples
        edge = data[:2*w] + data[-2*w:]
        edge += b''.join(data[y*w:y*w+2] + data[(y+1)*w-2:(y+1)*w] for y in range(2,h-2))
        if edge and min(edge) >= 200:
            return expanded, dict(scale=2, border_pixels=2, minimum_gray=min(edge),
                threshold=200, expansion_points=margin, crop_sha256=hashlib.sha256(data).hexdigest(),
                crop_size=[w,h])
    _need(False, 'table boundary crosses source ink')

def prepare(doc, raw, result):
    from .source_display import SourceDisplay
    raster, expected = render_input(doc, raw, version=result.get('version', VERSION))
    _need(isinstance(result, dict) and set(result) == set(expected) | {'detections'} and
          all(_json(result.get(k)) == _json(v) for k, v in expected.items()), 'source/model/raster binding mismatch')
    encoded = _json(result)
    _need(len(encoded.encode()) <= 65536, 'evidence bound')
    result = json.loads(encoded)
    detections = result['detections']
    _need(isinstance(detections, list) and len(detections) <= 256, 'detection count')
    entries, captions = [], []
    display = SourceDisplay(doc, raw.pno, dict(layer='ocr', **raw.source_geometry))
    for index, item in enumerate(detections):
        _need(isinstance(item, dict) and isinstance(item.get('label'), str), 'detection shape')
        confidence = item.get('confidence')
        _need(type(confidence) in (int, float) and math.isfinite(confidence) and
              0 <= confidence <= 1, 'confidence')
        pixel = _rect(item.get('bbox'))
        _need(_contains((0, 0, *expected['image_size']), pixel), 'outside raster')
        box = tuple(v * (raw.width, raw.height)[i%2] / expected['image_size'][i%2]
                    for i, v in enumerate(pixel))
        if item['label'] == 'figure_title':
            captions.append(box)
        label = 'table' if expected['version'] == REGION_VERSION else 'image'
        if item['label'] != label or confidence < MIN_CONFIDENCE:
            continue
        edge_proof = None
        if expected['version'] == REGION_VERSION:
            box, edge_proof = _table_pixel_bounds(display, box)
        pix = display.pixmap(box, scale=1, gray=True)
        _need(any(value < 245 for value in pix.samples), 'blank source artwork')
        proof = dict(expected, bound_result_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
                     detection_index=index, detection=item, source_bbox=list(box),
                     confidence_floor=MIN_CONFIDENCE)
        if edge_proof is not None:
            proof['pixel_boundary'] = edge_proof
        entries.append((box, _json(proof)))
        _need(len(entries) <= 32, 'candidate count')
    observation = _Observation(raw.pno, expected['raw_sha256'], tuple(entries), tuple(captions), expected['version'])
    _issued[observation] = observation.identity()
    return observation


def append_regions(raw, skel, observation, layout=None):
    from .skeleton import Region
    _need(type(observation) is _Observation and _issued.get(observation) == observation.identity()
          and observation.page == raw.pno == skel.pno and
          observation.raw_digest == _digest(asdict(raw)), 'unissued or stale observation')
    if observation.version == REGION_VERSION:
        return _append_tables(raw, skel, observation, layout)
    raw_lines = [line for block in raw.text_blocks for line in block.lines]
    references = {}
    for i, line in enumerate(raw_lines):
        references.setdefault(id(line), []).append(i)
    regions = list(skel.regions)
    removed, additions, unions = set(), [], []
    frame = (0, 0, raw.width, raw.height)
    for proposed, encoded in observation.entries:
        figures = [r for r in regions if r.kind == 'figure' and _overlap(proposed, _rect(r.bbox))]
        _need(bool(figures), 'coverage requires an existing figure')
        boxes = [proposed] + [_rect(r.bbox) for r in figures]
        union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes))
        _need(_contains(frame, union), 'union outside source frame')
        _need(not any(_overlap(union, b) for b in unions), 'conflicting candidate unions')
        _need(not any(_overlap(union, b) for b in observation.captions), 'caption intrusion')
        artwork = []
        for region in regions:
            box = _rect(region.bbox)
            if not _overlap(union, box):
                continue
            if any(region is r for r in figures):
                _need(not region.lines and not region.caption_lines and not region.image and
                      not region.list_groups, 'figure has unsupported source membership')
            elif region.kind == 'artwork':
                _need(_contains(union, box), 'partial artwork coverage')
                owners = [f for f in figures if _contains(f.bbox, box) and
                          (tuple(f.bbox) == tuple(box) or
                           f.reason == region.reason == 'ocr_uncertain_region')]
                _need(len(owners) == 1 and not region.caption_lines and not region.list_groups,
                      'ambiguous artwork owner')
                artwork.append(region)
            else:
                _need(False, 'foreign region or partial existing figure coverage')
        selected = figures + artwork
        for region in selected:
            _need(region.level == 0 and region.number is None and region.image is None and
                  region.continued_from is None and not region.initial_join and
                  not region.display_group and not region.list_groups and
                  not region.caption_lines and not region.visual_evidence,
                  'unsupported region semantic metadata')
            _need(region.reason in ('scan_figure_side', 'scan_figure_band', 'ocr_uncertain_region'),
                  'unsupported original region disposition')
        lines = [line for r in artwork for line in r.lines]
        ids = [id(line) for line in lines]
        _need(len(ids) == len(set(ids)), 'duplicate artwork membership')
        for line in lines:
            _need(len(references.get(id(line), [])) == 1 and _contains(union, _rect(line.bbox)),
                  'unmapped or cut source occurrence')
            memberships = [(r, ln) for r in regions for ln in r.lines + r.caption_lines if ln is line]
            _need(len(memberships) == 1, 'conflicting source ownership')
        for line in raw_lines:
            if _overlap(union, _rect(line.bbox)):
                _need(id(line) in ids, 'ordinary source text intrusion')
        # Keep the original immutable occurrence order, never transcribe OCR noise.
        lines.sort(key=lambda line: references[id(line)][0])
        _need(not any(id(r) in removed for r in selected), 'reused source region')
        proof = json.loads(encoded)
        proof.update(union_bbox=list(union), original_regions=[asdict(r) for r in selected],
                     source_occurrences=[dict(id='l%d' % references[id(line)][0],
                                              sha256=_digest(asdict(line))) for line in lines])
        band, column = layout.place(union) if layout else (figures[0].band, figures[0].column)
        uncertain = (any(r.reason == 'ocr_uncertain_region' or r.uncertain for r in selected) or
                     any(line.transcription_uncertain or line.spacing_uncertain or
                         any(sp.uncertain or sp.punctuation_uncertain or sp.encoding_unresolved or
                             sp.transcription_uncertain for sp in line.spans) for line in lines))
        reason = 'ocr_uncertain_region' if uncertain else 'local_visual_coverage'
        additions.append(Region('figure', bbox=union, reason=reason, needs_ink=True,
                                uncertain=uncertain, band=band, column=column, visual_evidence=proof))
        if lines:
            additions.append(Region('artwork', lines=lines, bbox=union, reason=reason,
                                    uncertain=uncertain, band=band, column=column))
        removed.update(id(r) for r in selected)
        unions.append(union)
    # Admission is atomic: no partial skeleton mutation on a later rejection.
    skel.regions[:] = [r for r in regions if id(r) not in removed] + additions


def _append_tables(raw, skel, observation, layout):
    """Bind complete table pixels and exact occurrences, never infer cell text.

    The model's table label remains advisory. Independent source checks require
    whole existing regions, unique immutable membership, all intersecting raw
    lines, and nonshrinking figure coverage. Captions and semantic structures
    stay outside. Rejected candidates never partially mutate the skeleton.
    """
    from .skeleton import Region
    raw_lines = [line for block in raw.text_blocks for line in block.lines]
    references = {}
    for i, line in enumerate(raw_lines):
        references.setdefault(id(line), []).append(i)
    regions = list(skel.regions)
    removed, additions, unions = set(), [], []
    for proposed, encoded in observation.entries:
        figures = [r for r in regions if r.kind == 'figure' and _overlap(proposed, _rect(r.bbox))]
        boxes = [proposed] + [_rect(r.bbox) for r in figures]
        union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes))
        _need(_contains((0, 0, raw.width, raw.height), union), 'table outside source frame')
        _need(not any(_overlap(union, b) for b in unions), 'conflicting table candidates')
        _need(not any(_overlap(union, b) for b in observation.captions), 'table caption intrusion')
        selected = [r for r in regions if _overlap(union, _rect(r.bbox))]
        _need(bool(selected), 'table has no source inventory')
        for region in selected:
            _need(_contains(proposed, region.bbox), 'partial table source region')
            _need(region.kind in ('body', 'artwork', 'figure'), 'table contains foreign semantic region')
            _need(region.level == 0 and region.number is None and region.image is None and
                  region.continued_from is None and not region.initial_join and
                  not region.display_group and not region.list_groups and
                  not region.caption_lines and not region.visual_evidence,
                  'unsupported table semantic metadata')
            if region.kind == 'figure':
                _need(not region.lines and region.reason in
                      ('scan_figure_side', 'scan_figure_band', 'ocr_uncertain_region'),
                      'unsupported table figure')
            elif region.kind == 'body':
                _need(not region.reason, 'unsupported table text disposition')
            else:
                _need(region.reason == 'ocr_uncertain_region', 'unsupported table artwork')
                owners = [r for r in figures if _contains(r.bbox, region.bbox)]
                _need(len(owners) == 1, 'ambiguous table artwork owner')
        lines = [line for r in selected for line in r.lines]
        ids = {id(line) for line in lines}
        _need(bool(lines) and len(lines) == len(ids), 'missing or duplicate table occurrences')
        for line in lines:
            _need(len(references.get(id(line), [])) == 1 and _contains(union, _rect(line.bbox)),
                  'unmapped or cut table occurrence')
            memberships = [r for r in regions for ln in r.lines + r.caption_lines if ln is line]
            _need(len(memberships) == 1, 'conflicting table source ownership')
        for line in raw_lines:
            if _overlap(union, _rect(line.bbox)):
                _need(id(line) in ids, 'unowned table source occurrence')
        _need(not any(id(r) in removed for r in selected), 'reused table source region')
        lines.sort(key=lambda line: references[id(line)][0])
        proof = json.loads(encoded)
        proof.update(union_bbox=list(union), original_regions=[asdict(r) for r in selected],
                     source_occurrences=[dict(id='l%d' % references[id(line)][0],
                                              sha256=_digest(asdict(line))) for line in lines],
                     representation='qualified_source_pixels', semantic_status='advisory')
        band, column = layout.place(union) if layout else (selected[0].band, selected[0].column)
        # This is a complete pixel representation, not a claimed transcription
        # or verified set of table cells. Every raw flag remains on its line.
        additions.append(Region('figure', bbox=union, reason='source_visual_table',
                                uncertain=True, band=band, column=column, visual_evidence=proof))
        additions.append(Region('artwork', lines=lines, bbox=union, reason='source_visual_table',
                                uncertain=True, band=band, column=column))
        removed.update(id(r) for r in selected)
        unions.append(union)
    skel.regions[:] = [r for r in regions if id(r) not in removed] + additions


def required_images(book, pages, doc):
    """Join the existing mandatory-source publication gate for admitted tables."""
    from . import source_inventory
    from .source_display import SourceDisplay
    required = {}
    for pno in pages:
        figures = [f for f in book.figures if f['pno'] == pno]
        inventory = book.source_inventory.get(pno, {})
        table_regions = {r['id'] for r in inventory.get('regions', [])
                         if r['suggested_kind'] == 'figure' and r['reason'] == 'source_visual_table'}
        for asset in inventory.get('assets', []):
            if asset['region_id'] in table_regions:
                local_index = int(asset['id'][1:])
                _need(local_index < len(figures) and
                      figures[local_index].get('visual_evidence', {}).get('version') == REGION_VERSION,
                      'table publication figure missing')
        for index, figure in enumerate(figures):
            proof = figure.get('visual_evidence', {})
            if proof.get('version') != REGION_VERSION:
                continue
            _need(doc is not None and not doc.is_dirty and bool(doc.name) and
                  extract.document_fingerprint(doc) == proof.get('pdf_sha256'),
                  'table publication PDF changed')
            page = doc[pno]
            _need(proof.get('page') == pno and page.rotation in (0, 90) and
                  list(page.mediabox) == proof.get('mediabox') and
                  list(page.cropbox) == proof.get('cropbox') and
                  list(page.derotation_matrix) == proof.get('derotation') and
                  proof.get('source_geometry') == dict(space='reading', orientation=0,
                      source_rotation=page.rotation, page_rect=list(page.rect)),
                  'table publication frame changed')
            inv = book.source_inventory.get(pno)
            source_inventory.validate(inv)
            _need(inv['raw_sha256'] == proof.get('raw_sha256') and
                  list(figure['bbox']) == proof.get('union_bbox') and
                  figure.get('found') == 'source_visual_table' and
                  figure.get('source_geometry', {}) == inv['source_geometry'],
                  'table publication ownership changed')
            assets = [a for a in inv['assets'] if a['id'] == 'f%d' % index]
            occurrences = proof.get('source_occurrences', [])
            _need(len(assets) == 1 and list(assets[0]['bbox']) == proof['union_bbox'] and
                  set(assets[0]['covered_line_ids']) == {r['id'] for r in occurrences} and
                  all(any(row['id'] == r['id'] and row['sha256'] == r['sha256']
                          for row in inv['lines']) for r in occurrences),
                  'table publication source inventory changed')
            display = SourceDisplay(doc, pno, dict(layer='ocr', **proof['source_geometry']))
            # Recheck the exact detector raster, including native-child execution.
            raster = display.jpeg(scale=1.5, quality=78)
            _need(hashlib.sha256(raster).hexdigest() == proof.get('image_sha256'),
                  'table publication raster changed')
            required['images/fig_p%04d_%d.jpg' % (pno, index)] = figure
    return required
