"""Capture chosen-source occurrences without changing assembly or granting authority.

Private source values may include image-owned transcripts. Consumers must project
ownership, never send this accounting record wholesale to a layout model.
"""
import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass

from . import extract

VERSION = 'source-ownership-capture-2'


def digest(value):
    def default(item):
        if is_dataclass(item):
            return asdict(item)
        raise TypeError('unsupported source inventory value')
    return hashlib.sha256(json.dumps(value, default=default, sort_keys=True,
        ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass
class _Catalog:
    page: dict
    references: dict


def catalog(raw):
    """Freeze exact source before repairs; object IDs are temporary lookup only."""
    rows, refs = [], {}
    for bi, block in enumerate(raw.blocks):
        if block.kind != 'text':
            continue
        for li, line in enumerate(block.lines):
            identifier = 'l%d' % len(rows)
            refs.setdefault(id(line), []).append(identifier)
            rows.append(dict(id=identifier, block_index=bi, line_index=li,
                source=copy.deepcopy(line), sha256=digest(line)))
    page = dict(version=VERSION, page=raw.pno, raw_sha256=digest(raw),
        source_geometry=copy.deepcopy(raw.source_geometry), lines=rows,
        raw_images=copy.deepcopy(raw.images), source_links=copy.deepcopy(raw.source_links))
    return _Catalog(page, refs)


def _ownership(lines, regions, assets):
    owners, unresolved = [], []
    for row in lines:
        identifier = row['id']
        memberships = [r for r in regions if identifier in
                       set(r['line_ids'] + r['caption_line_ids'])]
        artwork = [r for r in memberships if r['suggested_kind'] == 'artwork']
        owner = None
        representation = 'unresolved'
        if any(r['geometry_status'] != 'valid' for r in memberships):
            pass  # Retain words, but do not grant a malformed region ownership.
        elif artwork:
            # Exact unique paired region is conservative evidence, never a
            # generic intersection. Conflicting text/caption ownership is held.
            candidates = [a for a in assets if _region_geometry(a['bbox']) == 'valid' and
                          any(a['bbox'] == r['bbox'] for r in artwork)]
            if len(artwork) == 1 and len(candidates) == 1 and len(memberships) == 1:
                owner = candidates[0]['id']; representation = 'protected_image'
        elif len(memberships) == 1:
            region = memberships[0]
            if region['suggested_kind'] in ('body', 'heading', 'furniture', 'note', 'caption', 'list'):
                owner = region['id']; representation = 'text'
            # Figure caption lines are explicit text, not hidden artwork.
            elif region['suggested_kind'] == 'figure' and identifier in region['caption_line_ids']:
                owner = region['id']; representation = 'text'
        if owner is None:
            unresolved.append(dict(line_id=identifier, reason='ambiguous_or_missing_owner'))
        owners.append(dict(line_id=identifier, representation=representation, owner_id=owner))
    for region in regions:
        if region['geometry_status'] != 'valid':
            unresolved.append(dict(region_id=region['id'], reason='invalid_region_geometry'))
        if region['unmapped_lines']:
            unresolved.append(dict(region_id=region['id'], reason='unmapped_source_lines'))
    return owners, unresolved


def capture(prepared, skel, figure_offset=0):
    """Capture final region membership; labels remain deterministic suggestions."""
    page = copy.deepcopy(prepared.page)
    if page['page'] != skel.pno:
        raise ValueError('source inventory page differs')
    regions, assets = [], []
    for index, region in enumerate(skel.regions):
        unmapped = []
        def refs(lines):
            result = []
            for line in lines:
                found = prepared.references.get(id(line), ())
                if len(found) != 1:
                    unmapped.append(copy.deepcopy(line))
                else:
                    result.append(found[0])
            return result
        record = dict(id='r%d' % index, suggested_kind=region.kind, reason=region.reason,
            bbox=tuple(region.bbox), geometry_status=_region_geometry(region.bbox),
            line_ids=refs(region.lines),
            caption_line_ids=refs(region.caption_lines),
            list_line_groups=[refs(group) for group in region.list_groups],
            uncertain=bool(region.uncertain), image_descriptor=copy.deepcopy(region.image),
            unmapped_lines=unmapped)
        regions.append(record)
        if region.kind == 'figure':
            assets.append(dict(id='f%d' % len(assets), region_id=record['id'],
                bbox=record['bbox'], source_geometry=copy.deepcopy(page['source_geometry']),
                book_figure_index=figure_offset+len(assets), covered_line_ids=[],
                status='requires_emission'))
    owners, unresolved = _ownership(page['lines'], regions, assets)
    for asset in assets:
        asset['covered_line_ids'] = [r['line_id'] for r in owners
            if r['representation'] == 'protected_image' and r['owner_id'] == asset['id']]
    page.update(regions=regions, assets=assets, ownership=owners, unresolved=unresolved)
    page['sha256'] = digest(page)
    validate(page)
    return page


def _require(condition):
    if not condition:
        raise ValueError('invalid source inventory')


def _region_geometry(box):
    # Derived skeleton rectangles can have inverted extents (book566 p31).
    # Preserve the finite source value for diagnosis; never normalize it into
    # a usable crop or let this additive accounting abort legacy assembly.
    _require(isinstance(box, (tuple, list)) and len(box) == 4 and
        all(type(n) in (int, float) and math.isfinite(n) for n in box))
    return 'valid' if box[0] <= box[2] and box[1] <= box[3] else 'invalid_extent'


def _box(box):
    _require(_region_geometry(box) == 'valid')


def _line(line):
    _require(type(line) is extract.Line and type(line.spans) is list)
    _box(line.bbox)
    for name in ('spacing_uncertain', 'transcription_uncertain'):
        _require(type(getattr(line, name)) is bool)
    for span in line.spans:
        _require(type(span) is extract.Span and type(span.text) is str and type(span.font) is str)
        _box(span.bbox)
        _require(type(span.flags) is int)
        for name in ('size', 'origin_y'):
            _require(type(getattr(span,name)) in (int,float) and math.isfinite(getattr(span,name)))
        for name in ('uncertain','punctuation_uncertain','encoding_unresolved','transcription_uncertain'):
            _require(type(getattr(span,name)) is bool)
        _require(isinstance(span.char_boxes, (tuple,list)))
        for box in span.char_boxes:
            _require(isinstance(box, (tuple,list)) and len(box)==6 and
                type(box[0]) is int and type(box[1]) is int and
                0 <= box[0] < box[1] <= len(span.text))
            _box(box[2:])


def validate(page, raw=None):
    """Check integrity/relations; current raw also detects publicly re-sealed edits.

    Without raw this is an integrity check, not proof of factory issuance. Region
    classification still needs the child's current preparation at layout admission.
    """
    _require(type(page) is dict and set(page) == {'version','page','raw_sha256',
        'source_geometry','lines','raw_images','source_links','regions','assets',
        'ownership','unresolved','sha256'})
    _require(page['version'] == VERSION and type(page['page']) is int and page['page'] >= 0)
    _require(page['sha256'] == digest({k:v for k,v in page.items() if k!='sha256'}))
    _require(type(page['source_geometry']) is dict)
    for key in ('lines','raw_images','source_links','regions','assets','ownership','unresolved'):
        _require(type(page[key]) is list)
    for image in page['raw_images']:
        _require(type(image) is extract.Image)
        _box(image.bbox)
        _require(type(image.area_ratio) in (int,float) and math.isfinite(image.area_ratio) and
                 image.area_ratio >= 0 and type(image.page_background) is bool)
    _require(all(type(link) is dict for link in page['source_links']))
    positions = set()
    for i,row in enumerate(page['lines']):
        _require(type(row) is dict and set(row)=={'id','block_index','line_index','source','sha256'})
        _require(row['id']=='l%d'%i and all(type(row[k]) is int and row[k]>=0 for k in ('block_index','line_index')))
        position=(row['block_index'],row['line_index'])
        _require(position not in positions); positions.add(position)
        _line(row['source']); _require(row['sha256']==digest(row['source']))
    ids={r['id'] for r in page['lines']}
    for i,region in enumerate(page['regions']):
        _require(type(region) is dict and set(region)=={'id','suggested_kind','reason','bbox','geometry_status',
            'line_ids','caption_line_ids','list_line_groups','uncertain','image_descriptor','unmapped_lines'})
        _require(region['id']=='r%d'%i and region['suggested_kind'] in
                 ('body','heading','furniture','note','caption','list','artwork','figure'))
        _require(type(region['reason']) is str and type(region['uncertain']) is bool)
        _require(region['geometry_status'] == _region_geometry(region['bbox']))
        _require(type(region['list_line_groups']) is list)
        for refs in [region['line_ids'],region['caption_line_ids'],*region['list_line_groups']]:
            _require(type(refs) is list and all(type(x) is str and x in ids for x in refs) and len(refs)==len(set(refs)))
        _require(all(set(group)<=set(region['line_ids']) for group in region['list_line_groups']))
        _require(type(region['unmapped_lines']) is list)
        for line in region['unmapped_lines']: _line(line)
        _require(region['image_descriptor'] is None or type(region['image_descriptor']) is extract.Image)
    figures=[r for r in page['regions'] if r['suggested_kind']=='figure']
    _require(len(figures)==len(page['assets']))
    for i,(asset,region) in enumerate(zip(page['assets'],figures)):
        _require(type(asset) is dict and set(asset)=={'id','region_id','bbox','source_geometry',
            'book_figure_index','covered_line_ids','status'})
        _require(asset['id']=='f%d'%i and asset['region_id']==region['id'] and asset['bbox']==region['bbox'])
        _require(type(asset['book_figure_index']) is int and asset['book_figure_index']>=0 and
                 asset['source_geometry']==page['source_geometry'] and asset['status']=='requires_emission')
        if i: _require(asset['book_figure_index']==page['assets'][i-1]['book_figure_index']+1)
    owners,unresolved=_ownership(page['lines'],page['regions'],page['assets'])
    _require(page['ownership']==owners and page['unresolved']==unresolved)
    for asset in page['assets']:
        _require(asset['covered_line_ids']==[r['line_id'] for r in owners
            if r['representation']=='protected_image' and r['owner_id']==asset['id']])
    if raw is not None:
        current=catalog(raw).page
        _require(all(digest(page[k])==digest(v) for k,v in current.items()))
    return page
