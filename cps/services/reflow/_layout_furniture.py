"""Conservative source-owned additions for assembly-retained running furniture.

Only safe existing source spans are rendered. Unsupported lines remain private
accounting and block this page's layout admission; no guessed text or links.
"""
import html
import json
import re
import math
from xml.etree import ElementTree as ET
from . import source_inventory, transcript

VERSION = 'retained-furniture-source-4'
OPAQUE_COVERAGE_VERSION = 'opaque-furniture-coverage-1'


def _box(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)
            and value[0] < value[2] and value[1] < value[3])


def _covered_rectangle(target, boxes):
    """Every positive-width slab must cover the full height; no gap tolerance."""
    xs = sorted({target[0], target[2]} | {
        max(target[0], min(target[2], x)) for b in boxes for x in (b[0], b[2])})
    for left, right in zip(xs, xs[1:]):
        if right <= left:
            continue
        intervals = sorted((b[1], b[3]) for b in boxes
                           if b[0] <= left and b[2] >= right)
        reached = target[1]
        for low, high in intervals:
            if low > reached:
                break
            reached = max(reached, high)
        if reached < target[3]:
            return False
    return True


def _opaque_coverage(book, pno, source, raw, inventory, line, identifier, doc):
    """Account for existing mandatory pixels, without projecting another word.

    Only the untransformed PDF frame is currently supported. Each overlapping
    figure must have unique inventory/assembly/canonical-resource authority.
    Publication still has to emit every protected image or reject the layout.
    """
    try:
        if not _box(line.bbox) or raw.source_geometry or inventory['source_geometry']:
            return None
        page = doc[pno]
        provenance = json.loads(source.provenance_json)
        if (provenance.get('layer') != 'native' or provenance.get('pno', pno) != pno
                or provenance.get('page_rect', list(page.rect)) != list(page.rect)
                or page.rotation or page.rect.x0 or page.rect.y0
                or page.cropbox.x0 or page.cropbox.y0
                or abs(page.rect.width - raw.width) > 1e-6
                or abs(page.rect.height - raw.height) > 1e-6
                or provenance.get('orientation', 0) != 0
                or provenance.get('source_rotation', 0) != 0
                or provenance.get('derotation', [1, 0, 0, 1, 0, 0]) != [1, 0, 0, 1, 0, 0]):
            return None
        if any(_overlap(line.bbox, b) for el in book.pages[pno] if el.kind != 'fig'
               for b in (el.line_boxes or ([el.bbox] if el.bbox else []))):
            return None
        if any(n.pno == pno and _overlap(line.bbox, n.bbox) for n in book.notes):
            return None
        root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + source.html + '</root>')
        images = [n.get('src') for n in root.iter() if n.tag.rsplit('}', 1)[-1] == 'img']
        from ._layout_atoms import is_raster_block
        figure_images = [n.get('src') for f in root.iter()
                         if f.tag.rsplit('}', 1)[-1] == 'figure' or is_raster_block(f)
                         for n in f.iter() if n.tag.rsplit('}', 1)[-1] == 'img']
        page_figures = [f for f in book.figures if f['pno'] == pno]
        if ([tuple(e.bbox) for e in book.pages[pno] if e.kind == 'fig']
                != [tuple(f['bbox']) for f in page_figures]):
            return None
        prefix = 'images/fig_p%04d_' % pno
        if ([uri for uri in figure_images if uri and uri.startswith(prefix)]
                != [prefix + '%d.jpg' % i for i in range(len(page_figures))]):
            return None
        regions = {r['id']: r for r in inventory['regions']}
        proof = []; boxes = []; local_index = -1
        for index, figure in enumerate(book.figures):
            if figure['pno'] != pno:
                continue
            local_index += 1
            bounds = figure['bbox']
            if not _box(bounds):
                return None
            if not _overlap(line.bbox, bounds):
                continue
            assets = [a for a in inventory['assets']
                      if type(a.get('book_figure_index')) is int and a['book_figure_index'] == index]
            if (len(assets) != 1 or figure.get('needs_ink') is not True
                    or figure.get('source_geometry') or bounds[0] < 0 or bounds[1] < 0
                    or bounds[2] > raw.width or bounds[3] > raw.height):
                return None
            asset = assets[0]; region = regions[asset['region_id']]
            uri = 'images/fig_p%04d_%d.jpg' % (pno, local_index)
            if (asset['status'] != 'requires_emission' or asset['source_geometry']
                    or tuple(asset['bbox']) != tuple(bounds)
                    or region['suggested_kind'] != 'figure' or region['geometry_status'] != 'valid'
                    or tuple(region['bbox']) != tuple(bounds)
                    or images.count(uri) != 1 or figure_images.count(uri) != 1
                    or sum(e.kind == 'fig' and tuple(e.bbox) == tuple(bounds)
                           for e in book.pages[pno]) != 1):
                return None
            boxes.append(bounds)
            proof.append(dict(asset_id=asset['id'], region_id=asset['region_id'],
                              book_figure_index=index, bbox=list(bounds), resource=uri))
        if not boxes or not _covered_rectangle(line.bbox, boxes):
            return None
        return dict(line_id=identifier, source_sha256=source_inventory.digest(line),
                    bbox=list(line.bbox), figures=proof)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, ET.ParseError):
        return None


def _overlap(a, b):
    return bool(a and b and len(a)==len(b)==4 and
        min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1]))


def _corroborated_native(provenance, regional=False):
    evidence = provenance.get('verification', {})
    return (provenance.get('layer') == 'native'
        and provenance.get('reason') == 'verify_scan_transcript'
        and 'native_transcript_corroborated' in provenance.get('flags', ())
        and evidence.get('version') == transcript.VERSION
        and all(type(evidence.get(k)) is int and evidence[k] >= 0 for k in
            ('words', 'unmatched_words', 'uncertain_words', 'uncertain_lines', 'lines'))
        and (evidence['unmatched_words'] == 0 or regional)
        and evidence.get('recognition_orientation') in (0, 90, 180, 270)
        and bool(re.fullmatch(r'[0-9a-f]{64}', str(evidence.get('recognition_sha256', '')))))


def _records_clear(records, provenance, box):
    # Records use displayed source coordinates; only an explicitly unrotated
    # frame can be compared directly to selected source lines here. Canonical
    # annotations have already been rendered and are never rematched.
    if not records:
        return True
    if provenance.get('orientation', 0) != 0:
        return False
    for record in records:
        bounds = record.get('source_bbox')
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4
                or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in bounds)
                or bounds[2] <= bounds[0] or bounds[3] <= bounds[1] or _overlap(bounds, box)):
            return False
    return True


def _navigation_clear(book, pno, raw, box, doc):
    links = list(raw.source_links)
    incoming = []
    for link in book.source_navigation:
        if link.get('pno') == pno:
            links.append(link)
        if link.get('dest_page') == pno:
            incoming.append(link)
    if not links and not incoming:
        return True
    # PyMuPDF navigation coordinates are displayed/rotated, while native
    # text lines use unrotated coordinates. Do not compare unknown frames.
    try:
        if doc is None or doc[pno].rotation != 0:
            return False
    except (KeyError, IndexError, TypeError, AttributeError):
        return False
    geometry = getattr(raw, 'source_geometry', {})
    if geometry.get('space') == 'reading' and geometry.get('orientation', 0) != 0:
        return False
    for link in links:
        rect = link.get('rect')
        if (not isinstance(rect, (list, tuple)) or len(rect) != 4
                or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in rect)
                or rect[2] <= rect[0] or rect[3] <= rect[1] or _overlap(rect, box)):
            return False
    for link in incoming:
        point = link.get('dest_point')
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in point)
                or (box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3])):
            return False
    return True


def project(book, pno, source_page, raw_page, inventory, doc=None, covered=None):
    if inventory is None:
        # Old furniture strings have no page/occurrence binding; never guess.
        return [], ['furniture_inventory_missing'] if book.furniture else []
    provenance=json.loads(source_page.provenance_json)
    from . import transcript_regions
    regional = transcript_regions.verify_source(book, pno, provenance, source_page.html)
    corroborated = _corroborated_native(provenance, regional)
    records=json.loads(source_page.records_json)
    lines={row['id']:row for row in inventory['lines']}
    owners={row['line_id']:row for row in inventory['ownership']}
    projections=[];failures=[];seen=set()
    for region in inventory['regions']:
        if region['suggested_kind']!='furniture':continue
        reason=None
        if (region['geometry_status']!='valid' or region['unmapped_lines'] or
                not region['line_ids'] or region['caption_line_ids'] or region['list_line_groups']):
            reason='furniture_membership_unsupported'
        elif getattr(raw_page,'transcript_unverified',False) and not (regional and corroborated):
            reason='furniture_transcript_unverified'
        elif (getattr(raw_page,'text_layer_invisible',False) or
              getattr(raw_page,'text_layer_overpainted',False)) and provenance.get('layer')!='ocr' and not corroborated:
            reason='furniture_hidden_native_layer'
        candidates=[]; image_covered=[]
        for identifier in region['line_ids']:
            row=lines[identifier];line=row['source'];owner=owners[identifier]
            if not _navigation_clear(book,pno,raw_page,line.bbox,doc):
                reason=reason or 'furniture_navigation_protection_required'
            if not _records_clear(records,provenance,line.bbox):
                reason=reason or 'furniture_uncertainty_protection_required'
            if identifier in seen or owner['representation']!='text' or owner['owner_id']!=region['id']:
                reason=reason or 'furniture_ownership_unsupported'
            if line.spacing_uncertain or line.transcription_uncertain or region['uncertain'] or any(
                s.uncertain or s.transcription_uncertain or s.encoding_unresolved or
                s.punctuation_uncertain or s.superscript for s in line.spans):
                reason=reason or 'furniture_source_protection_required'
            # Local ownership must not append an occurrence still represented
            # by a canonical element, note or source crop. Equal words elsewhere
            # are fine; overlapping source geometry is not a proof of identity.
            boxes=[]
            for element in book.pages[pno]:
                boxes.extend(element.line_boxes or ([element.bbox] if element.bbox else []))
            boxes.extend(n.bbox for n in book.notes if n.pno==pno and n.bbox)
            boxes.extend(f['bbox'] for f in book.figures if f['pno']==pno)
            if any(_overlap(line.bbox, box) for box in boxes):
                proof = (None if reason else _opaque_coverage(
                    book,pno,source_page,raw_page,inventory,line,identifier,doc))
                if proof is not None:
                    image_covered.append(proof)
                    seen.add(identifier)
                    continue
                reason=reason or 'furniture_canonical_overlap'
            parts=[]
            for span in line.spans:
                value=html.escape(span.text,quote=False)
                if span.italic:value='<em>'+value+'</em>'
                if span.bold:value='<strong>'+value+'</strong>'
                parts.append(value)
            candidates.append(dict(page=pno,line_id=identifier,region_id=region['id'],
                source_sha256=source_inventory.digest(line),bbox=line.bbox,
                html='<p>'+''.join(parts)+'</p>'))
            seen.add(identifier)
        if reason:
            failures.append(reason+':'+region['id'])
        else:
            projections.extend(candidates)
            if covered is not None:
                covered.extend(image_covered)
    return projections,failures
