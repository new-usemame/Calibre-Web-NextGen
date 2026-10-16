"""Opt-in, locally attested first-stage declarations; never layout ownership.

The caller supplies an independently accepted execution receipt (not one read from
an untrusted packet). Hashes anchor that execution, not semantic geometry: the
factory refreshes source authority and pixels, and reconstructs every relation.
Restored packets require the same factory. No model/utility runtime is imported.
"""
import hashlib
import json
import math
import weakref
from dataclasses import dataclass

from . import _layout_atoms as atoms, layout_ops, layout_construction
from .source_display import SourceDisplay

VERSION = 'layout-advisory-1'
PROJECTION_VERSION = 'layout-advisory-projection-1'
QUOTE_VERSION = 'layout-hint-quote-1'
MODEL = dict(model='MinerU2.5-Pro-2605-1.2B',
    model_revision='bff20d4ae2bf202df9f45284b4d43681555a97ed',
    utility_revision='405520b8fe6be61725537984eb4a00d404dc272a',
    weights_sha256='abf8681ca63b8dec7b67de257af47b821f179442f72998d0696ae2ed9232a5f0')
_MODEL_JSON = json.dumps(MODEL, sort_keys=True, separators=(',', ':'))
PROMPT = '''\nlayout_advisory contains raw trained first-stage declarations and COMPUTED whole-line geometric proxies only, not verified ownership, paragraphs, reading order or operations. raw rows=[sequence,type,bins,angle,merge_prev_marker]; bins are normalized thousandths. boxes rows=[sequence,source_bbox]. lines rows=[source_line,ALL positive-overlap sequences,center-inside sequences]. Each current ID inherits ALL relations of ALL its supplied printed_lines memberships; combined furniture/body lines cannot resolve individual words. absent_geometry, multiple_overlap and unmapped list EVERY such current integer ID explicitly. Absent glyph/notice geometry remains UNKNOWN; no interpolation, OCR, generated words, label/lexical/continuation invention or default fill. Model sequence/type/merge marker are advisory declarations, never mandatory order/roles/joins. Choose every complete group/range/order/join/incoming/association/emphasis yourself from FULL original source and raster; compiler and independent original-source review remain authoritative.'''
_TYPES = {'text','title','table','image','equation','header','footer','page_number','code','reference','list','caption'}
_issued = weakref.WeakKeyDictionary()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def declarations(raw):
    """Explicit ingestion allowlist: generated tails never enter the packet."""
    return [{k:r[k] for k in ('sequence','type','raw_bins','angle','merge_prev_model_marker')} for r in raw]


def source_binding(prepared):
    s=json.loads(prepared.contract_json)
    atoms.need(type(prepared) is layout_ops.PreparedLayout and s['snapshot']==prepared.snapshot_id
        and atoms.digest({k:v for k,v in s.items() if k!='snapshot'})==s['snapshot'], 'hint source snapshot differs')
    atoms.need(s['binding']['raster_sha256']==_hash(prepared.raster)
        and s['binding']['pdf_sha256']==prepared.pdf_digest, 'hint source image or PDF differs')
    return dict(page=prepared.page, snapshot=prepared.snapshot_id, source_identity=prepared.source_identity,
        pdf_sha256=prepared.pdf_digest, contract_sha256=_hash(prepared.contract_json.encode()),
        coverage_sha256=_hash(prepared.coverage_json.encode()), raster_sha256=_hash(prepared.raster))


def _receipt(prepared, raw, preprocessing, receipt):
    # This object is an authority supplied by the trusted experiment/local
    # producer. Never derive it from packet claims during restoration.
    expected=dict(version=VERSION, model=json.loads(_MODEL_JSON), source=source_binding(prepared),
        declarations_sha256=_hash(_json(raw).encode()), preprocessing_sha256=_hash(_json(preprocessing).encode()))
    atoms.need(type(receipt) is dict and receipt==expected, 'hint execution receipt differs')


def _body(prepared, raw, preprocessing, receipt, frame):
    _receipt(prepared,raw,preprocessing,receipt)
    from .model import _jpeg_dimensions
    wh=list(_jpeg_dimensions(prepared.raster) or ())
    atoms.need(type(raw) is list and len(raw)<=2048, 'hint declaration array required')
    atoms.need(preprocessing.get('original_raster_wh')==wh
        and preprocessing.get('layout_wh')==[1036,1036]
        and preprocessing.get('processor_effective_wh')==[1036,1036]
        and preprocessing.get('layout_resize')=='PIL BICUBIC; anisotropic full-page stretch; no crop/padding/rotation'
        and preprocessing.get('layout_scale_xy')==[1036/wh[0],1036/wh[1]]
        and preprocessing.get('processor_padding')=='token padding=True batch1; no pixel padding', 'unsupported hint preprocessing')
    atoms.need(set(frame)=={'scale','origin','raster_wh','reading_rect','display_version'}
        and frame['scale']==1.5 and frame['raster_wh']==wh
        and frame['display_version']=='source-display-1', 'hint frame differs')
    boxes=[]
    for i,r in enumerate(raw):
        atoms.need(type(r) is dict and set(r)=={'sequence','type','raw_bins','angle','merge_prev_model_marker'}
            and type(r['sequence']) is int and r['sequence']==i
            and r['type'] in _TYPES and type(r['angle']) is int and r['angle'] in (0,90,180,270)
            and type(r['merge_prev_model_marker']) is bool, 'invalid hint declaration')
        b=r['raw_bins']
        atoms.need(type(b) is list and len(b)==4 and all(type(v) is int and 0<=v<=1000 for v in b)
            and b[0]<b[2] and b[1]<b[3], 'invalid hint box')
        box=[(v/1000*wh[j%2]+frame['origin'][j%2])/frame['scale'] for j,v in enumerate(b)]
        boxes.append(dict(sequence=i,bbox=box))
    def overlap(a,b):
        return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    source=json.loads(prepared.contract_json);ids=[a['id'] for a in source['atoms']]
    atoms.need(ids==['a'+str(i) for i in range(len(ids))], 'hint IDs not bijective')
    lines=prepared.model_view().get('printed_lines',[]);members=layout_construction._memberships(source)
    relations=[];byline={}
    for line in lines:
        a=line['bbox']
        atoms.need(len(a)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in a)
            and a[0]<a[2] and a[1]<a[3] and line['source_line'] not in byline, 'hint line geometry differs')
        rr=[]
        for b in boxes:
            area=overlap(a,b['bbox'])
            if area>0:
                z=b['bbox'];rr.append(dict(sequence=b['sequence'],area=area,
                    fraction=area/((a[2]-a[0])*(a[3]-a[1])),
                    center_inside=z[0]<=(a[0]+a[2])/2<=z[2] and z[1]<=(a[1]+a[3])/2<=z[3]))
        row=dict(source_line=line['source_line'],relations=rr)
        byline[line['source_line']]=row;relations.append(row)
    records=[]
    for a in source['atoms']:
        ls=members[a['id']]
        atoms.need(all(l in byline for l in ls), 'hint membership lacks supplied line')
        seq=sorted({r['sequence'] for l in ls for r in byline[l]['relations']})
        status='absent_geometry' if not ls else 'unmapped' if not seq else 'multiple_overlap' if len(seq)>1 else 'single_overlap'
        records.append(dict(id=a['id'],source_lines=ls,sequences=seq,status=status,
            exact_token_box=None,protected=a['protected'],empty=not a['text']))
    return dict(version=VERSION,projection_version=PROJECTION_VERSION,receipt=receipt,frame=frame,
        raw_declarations=raw,preprocessing=preprocessing,
        computed=dict(boxes=boxes,lines=relations,atoms=records))


@dataclass(frozen=True, eq=False)
class LayoutHints:
    packet_json: str


def prepare(book, doc, prepared, *, declarations, preprocessing, receipt, source_page, raw_page,
            previous_source=None, previous_raw=None, packet=None):
    """Issue or restore against actual current factory authority, image and PDF."""
    prepared._current(book,doc,source_page,raw_page,previous_source,previous_raw)
    layer=json.loads(source_page.provenance_json)
    atoms.need(layer.get('layer')=='native' and not json.loads(prepared.contract_json)['binding'].get('raster_panels'),
        'hint frame currently supports native single full pages only')
    display=SourceDisplay(doc,prepared.page,layer)
    atoms.need(display.angle==0 and display.jpeg(scale=1.5,quality=85)==prepared.raster, 'hint raster not exact current display')
    pix=display.pixmap(scale=1.5)
    frame=dict(scale=1.5,origin=[pix.x,pix.y],raster_wh=[pix.width,pix.height],
        reading_rect=list(display.rect),display_version='source-display-1')
    body=_body(prepared,declarations,preprocessing,receipt,frame)
    encoded=_json(body)
    atoms.need(packet is None or type(packet) is dict and _json(packet)==encoded, 'hint packet schema or computed coverage differs')
    result=LayoutHints(encoded)
    _issued[result]=(encoded,source_binding(prepared))
    return result


def validate(prepared,hints):
    """Reject unissued/stale/tampered content; reconstruct, never trust a hash."""
    atoms.need(type(hints) is LayoutHints and hints in _issued, 'hint factory issuance required')
    encoded,binding=_issued[hints]
    atoms.need(hints.packet_json==encoded and source_binding(prepared)==binding, 'hint changed or stale')
    b=json.loads(encoded)
    actual=_body(prepared,b['raw_declarations'],b['preprocessing'],b['receipt'],b['frame'])
    atoms.need(_json(actual)==encoded, 'hint computed relations or coverage differs')
    return b


def projection(prepared,hints):
    b=validate(prepared,hints);c=b['computed']
    return dict(version=PROJECTION_VERSION, model=b['receipt']['model'],
        preprocessing_sha256=b['receipt']['preprocessing_sha256'],
        frame=b['frame'],
        raw=[[r['sequence'],r['type'],r['raw_bins'],r['angle'],r['merge_prev_model_marker']] for r in b['raw_declarations']],
        boxes=[[r['sequence'],r['bbox']] for r in c['boxes']],
        lines=[[r['source_line'],[x['sequence'] for x in r['relations']],
            [x['sequence'] for x in r['relations'] if x['center_inside']]] for r in c['lines']],
        **{status:[int(a['id'][1:]) for a in c['atoms'] if a['status']==status]
            for status in ('absent_geometry','multiple_overlap','unmapped')})


def identity(prepared,hints):
    b=validate(prepared,hints)
    return dict(version=VERSION,packet_sha256=_hash(hints.packet_json.encode()),
        projection_sha256=_hash(_json(projection(prepared,hints)).encode()))


def proposal(prepared,previous=None,hints=None):
    from . import layout_ranges
    request=layout_ranges.proposal(prepared,previous)
    if hints is None:return request
    view=json.loads(request['messages'][-1]['content'])
    view['layout_advisory']=projection(prepared,hints)
    request['source_identity']['layout_advisory']=identity(prepared,hints)
    request['prompt_version']+='-'+PROJECTION_VERSION
    # Preserve the neighbor suffix so quote guards can remove it exactly.
    prompt=request['messages'][0]['content']
    from .layout_requests import PREVIOUS_CANDIDATE_PROMPT
    if previous is not None:prompt=prompt[:-len(PREVIOUS_CANDIDATE_PROMPT)]
    request['messages'][0]['content']=prompt+PROMPT+(PREVIOUS_CANDIDATE_PROMPT if previous is not None else '')
    request['messages'][-1]['content']=_json(view)
    return request
