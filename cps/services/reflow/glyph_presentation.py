"""Complete source ink for a uniquely owned glyph; canonical resources stay sealed.

Text-layer rectangles are not ink extents. Only an opaque native bitonal page
whose complete rendered samples equal the original bitmap can supply this
publication projection. No recognition, word repair or layout decision occurs.
"""
import hashlib
import io
import json
import math
import os
from collections import Counter
from xml.dom import minidom
from xml.parsers.expat import ExpatError
from . import extract, native_text, source_inventory
from .source_display import SourceDisplay

VERSION = 'source-glyph-presentation-3'
INK_VERSION = 'source-glyph-presentation-1'
METRIC_VERSION = 'source-glyph-pdf-metrics-1'
MAX_QUERY_PIXELS = 250_000


def stylesheet():
    return '.source-glyph-presentation > .source-glyph-original { display:none; }\n'


def metric_stylesheet(evidence):
    """Serialize only metrics issued by this publication's current proof stage."""
    rules = {}
    for page in evidence:
        for entry in page.get('glyph_presentation', {}).get('entries', []):
            metric = entry.get('presentation', {})
            if metric.get('applied'):
                selector = metric['id']
                rules[selector] = ('.source-glyph-display[data-source-presentation="%s"] img '
                    '{ height:%.8fem !important; width:auto; max-width:none; vertical-align:%.8fem !important; }\n' %
                    (selector, metric['height_em'], metric['baseline_offset_em']))
    return ''.join(rules[key] for key in sorted(rules))


class _Refused(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise _Refused(reason)


def _box(box, sx, sy, width, height):
    return (max(0,math.floor(box[0]*sx)),max(0,math.floor(box[1]*sy)),
            min(width,math.ceil(box[2]*sx)),min(height,math.ceil(box[3]*sy)))


def _intersects(a,b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _presentation(span, proof):
    """Map PDF points to the local em; never measure or invent a reader face.

    The image bottom is placed at the retained PDF baseline plus its actual
    crop descent. This keeps whitespace and complete ink in their source
    proportions. A superscript's existing parent still supplies its local em.
    Missing metrics withhold this optional adjustment, not the proved ink.
    """
    row = dict(version=METRIC_VERSION, applied=False)
    size, baseline = span.size, span.origin_y
    top, bottom = proof['reading_bbox'][1], proof['reading_bbox'][3]
    if (not all(math.isfinite(v) for v in (size, baseline, top, bottom))
            or size <= 0 or baseline <= 0 or not top < baseline < bottom
            or not span.font or span.font == 'mixed-native-fonts'):
        row['reason'] = 'retained_font_size_or_baseline_unproved'
        return row
    row.update(applied=True, source_size=size, source_baseline=baseline,
        height_em=(bottom-top)/size, baseline_offset_em=(baseline-bottom)/size,
        proof='current_retained_span_and_native_crop_points')
    row['id'] = hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
    return row


def _complete_ink(image, rect, size, display, other_boxes):
    """Follow every seeded 8-connected island; refuse competing source spans.

    The halo is a work bound, never permission to truncate a component. Every
    island must close within it, and the expanded rectangle must contain only
    those seeded islands. This also refuses detached ink newly encountered in
    an expansion: text identity alone cannot authorize taking those pixels.
    """
    sx,sy=image.width/display.rect.width,image.height/display.rect.height
    seed=_box(rect,sx,sy,image.width,image.height)
    halo=_box((rect[0]-size,rect[1]-size,rect[2]+size,rect[3]+size),sx,sy,image.width,image.height)
    x0,y0,x1,y1=halo;w,h=x1-x0,y1-y0
    _need(0 < w*h <= MAX_QUERY_PIXELS,'ink_query_exceeds_bound')
    pixels=image.crop(halo).convert('L').tobytes()
    remaining=bytearray(v==0 for v in pixels)
    chosen=set();extents=[]
    for y in range(max(seed[1],y0),min(seed[3],y1)):
        for x in range(max(seed[0],x0),min(seed[2],x1)):
            start=(y-y0)*w+x-x0
            if not remaining[start]:continue
            remaining[start]=0;stack=[start];component=[]
            left=right=x;top=bottom=y
            while stack:
                pos=stack.pop();yy,xx=divmod(pos,w);component.append(pos)
                left=min(left,xx+x0);right=max(right,xx+x0)
                top=min(top,yy+y0);bottom=max(bottom,yy+y0)
                for ny in range(max(0,yy-1),min(h,yy+2)):
                    for nx in range(max(0,xx-1),min(w,xx+2)):
                        q=ny*w+nx
                        if remaining[q]:remaining[q]=0;stack.append(q)
            box=(left,top,right+1,bottom+1)
            _need(left>x0 and top>y0 and right<x1-1 and bottom<y1-1,'ink_component_not_closed')
            # Bounding boxes are deliberately conservative: even a possible
            # overlap with another retained span withholds display authority.
            _need(not any(_intersects(box,_box(b,sx,sy,image.width,image.height)) for b in other_boxes),
                  'ink_crosses_neighbor_span')
            chosen.update(component);extents.append(box)
    _need(extents,'no_source_ink')
    crop=(min(seed[0],*(b[0]-1 for b in extents)),min(seed[1],*(b[1]-1 for b in extents)),
          max(seed[2],*(b[2]+1 for b in extents)),max(seed[3],*(b[3]+1 for b in extents)))
    # A crop that already contains its complete ink still needs the same
    # source baseline/size projection as one whose ink required expansion.
    # Both paths must prove ownership and retain the sealed original below.
    _need(x0<=crop[0] and y0<=crop[1] and crop[2]<=x1 and crop[3]<=y1,'ink_component_not_closed')
    for y in range(crop[1],crop[3]):
        for x in range(crop[0],crop[2]):
            q=(y-y0)*w+x-x0
            _need(pixels[q]!=0 or q in chosen,'expanded_crop_has_unowned_ink')
    stream=io.BytesIO();image.crop(crop).save(stream,format='PNG',optimize=True)
    return stream.getvalue(),dict(native_seed_box=list(seed),native_display_box=list(crop),
        ink_components=[list(b) for b in extents],ink_pixels=len(chosen),
        reading_bbox=[crop[0]/sx,crop[1]/sy,crop[2]/sx,crop[3]/sy])


def render(book, doc, source, raw, fragment, package):
    """Current factory/raw ownership authorizes a display, never saved audits."""
    source.validate(book)
    inventory=book.source_inventory.get(source.page)
    records={}
    runs=[r for e in book.pages[source.page] for r in e.runs]
    runs += [r for n in book.notes if n.pno==source.page for r in n.glyph_runs]
    runs += [['glyph',n.text,native_text.descriptor(n.pno,n.bbox,0,'note')]
             for n in book.notes if n.pno==source.page and n.glyph_fallback and not n.glyph_runs]
    for run in runs:
        if run[0]=='glyph':
            name=native_text.image_name(run[2]);records.setdefault(name,[]).append(run)
    audit=dict(version=VERSION,source_identity=source.identity,
        generated_class='source-glyph-display',canonical_glyphs_retained=True,
        excluded_from_source_conservation=True,entries=[])
    if not records:return fragment,audit
    if raw is not None and inventory is not None:source_inventory.validate(inventory,raw)
    parse=lambda value:minidom.parseString('<root xmlns:epub="http://www.idpf.org/2007/ops">'+value+'</root>')
    # Publication can contain explicit evidence for XML-forbidden source
    # codepoints. Canonical source remains sealed; it must never be cleaned
    # to authorize this optional projection. Refuse and keep normal output.
    try:
        canonical=parse(source.html)
    except ExpatError:
        audit['entries']=[dict(source_resource=name,displayed=False,
            reason='canonical_source_markup_unparseable') for name in records]
        return fragment,audit
    try:
        dom=parse(fragment)
    except ExpatError:
        audit['entries']=[dict(source_resource=name,displayed=False,
            reason='publication_fragment_markup_unparseable') for name in records]
        return fragment,audit
    anchors=lambda tree:[n for n in tree.getElementsByTagName('a') if n.getAttribute('class')=='source-glyph']
    expected=Counter(n.toxml() for n in anchors(canonical));actual=Counter(n.toxml() for n in anchors(dom))
    filename=getattr(doc,'name',None)
    bound=bool(book.source_fingerprint and filename and os.path.isfile(filename)
        and not getattr(doc,'is_dirty',True)
        and extract.document_fingerprint(doc)==book.source_fingerprint)
    audit['source_pdf_binding_verified']=bound
    display=SourceDisplay(doc,source.page);image=display._native_bitonal() if bound else None
    for name,occurrences in records.items():
        row=dict(source_resource=name,displayed=False);audit['entries'].append(row)
        try:
            _need(raw is not None and inventory is not None,'retained_raw_or_inventory_missing')
            _need(bound,'immutable_source_pdf_binding_unproved')
            _need(image is not None,'native_bitonal_visible_page_unproved')
            _need(all(r==occurrences[0] for r in occurrences),'ambiguous_glyph_descriptor')
            _,text,record=occurrences[0]
            matches=[(line,sp) for line in inventory['lines'] for sp in line['source'].spans
                if sp.text==text and native_text.descriptor(source.page,sp.bbox,sp.size,sp.font,
                    raised=record.get('raised',False),reason=record.get('reason'))==record
                and (sp.encoding_unresolved or sp.transcription_uncertain)]
            _need(len(matches)==1,'ambiguous_or_missing_retained_span')
            line,span=matches[0]
            owners=[o for o in inventory['ownership'] if o['line_id']==line['id']]
            _need(len(owners)==1 and owners[0]['representation']=='text','glyph_line_not_uniquely_text_owned')
            region=next(r for r in inventory['regions'] if r['id']==owners[0]['owner_id'])
            _need(region['geometry_status']=='valid' and not region['unmapped_lines'],'unsupported_region_membership')
            original=anchors(parse(native_text.glyph_html(record)))[0].toxml()
            _need(expected[original]>0 and actual[original]==expected[original],'current_glyph_tree_differs')
            others=[sp.bbox for ln in inventory['lines'] for sp in ln['source'].spans
                    if sp is not span and sp.text.strip()]
            data,proof=_complete_ink(image,record['bbox'],record['size'],display,others)
            # Pixel identity is unchanged by a new presentation metric version.
            projected=dict(record,display_projection=INK_VERSION,native_display_box=proof['native_display_box'])
            src=native_text.image_name(projected)
            presentation = _presentation(span, proof)
            _need(package is not None,'resource_sink_missing')
            package.image(src,data)
            for node in list(anchors(dom)):
                if node.toxml()!=original:continue
                wrapper=dom.createElement('span');wrapper.setAttribute('class','source-glyph-presentation')
                retained=dom.createElement('span');retained.setAttribute('class','source-glyph-original')
                visible=dom.createElement('span');visible.setAttribute('class','source-glyph-display')
                enhanced=node.cloneNode(True);img=enhanced.getElementsByTagName('img')[0]
                img.setAttribute('src',src)
                if presentation['applied']:
                    visible.setAttribute('data-source-presentation',presentation['id'])
                node.parentNode.replaceChild(wrapper,node);retained.appendChild(node);visible.appendChild(enhanced)
                wrapper.appendChild(retained);wrapper.appendChild(visible)
            row.update(displayed=True,display_resource=src,display_sha256=hashlib.sha256(data).hexdigest(),
                line_id=line['id'],owner_id=owners[0]['owner_id'],proof=proof,
                presentation=presentation)
        except _Refused as exc:
            row['reason']=str(exc)
    return (''.join(n.toxml() for n in dom.documentElement.childNodes) if any(r['displayed'] for r in audit['entries']) else fragment),audit
