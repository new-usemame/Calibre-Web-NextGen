"""Occurrence-bound alternative readings, separate from unchanged Recovery.

Only current factory preparation and independent bound review issue evidence.
Canonical text is never edited; these records have source-detail placement only.
"""
import hashlib
import json
import math
import unicodedata
from dataclasses import asdict,dataclass,replace
from . import enriched_source,extract
from .structural_ops import ContractError,_digest

VERSION='source-readings-2'
_issued=enriched_source._IdentityWeakRegistry()

def need(condition,reason):
    if not condition:raise ContractError('source readings: '+reason)

def encoded(value):
    try:return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError) as exc:raise ContractError('source readings: invalid evidence values') from exc

def sha(data):return hashlib.sha256(data).hexdigest()

@dataclass(frozen=True,eq=False)
class PreparedReadings:
    binding_json:str
    raster:bytes
    crops:tuple
    @property
    def snapshot(self):return _digest(json.loads(self.binding_json))
    def request(self):return dict(json.loads(self.binding_json),snapshot=self.snapshot)

@dataclass(frozen=True,eq=False)
class ReadingEvidence:
    proof_json:str
    def identity(self):return _digest(self.proof_json)


_RANGE_FIELDS=('block','line','span','start','end')

def _selector(occurrence):
    if 'segments' in occurrence:
        return dict(original=occurrence['original'],segments=occurrence['segments'])
    return {k:occurrence[k] for k in (*_RANGE_FIELDS,'original')}

def _segments(selected):
    need(isinstance(selected,dict),'occurrence fields')
    if set(selected)=={'segments','original'}:
        segments=selected['segments']
        need(isinstance(segments,list) and 1<=len(segments)<=64,'segment count')
    else:
        need(set(selected)==set((*_RANGE_FIELDS,'original')),'occurrence fields')
        segments=[{k:selected[k] for k in _RANGE_FIELDS}]
    for segment in segments:
        need(isinstance(segment,dict) and set(segment)==set(_RANGE_FIELDS),'segment fields')
        need(all(type(segment[k]) is int and segment[k]>=0 for k in _RANGE_FIELDS),'occurrence indices')
    return segments

def _bbox(boxes):
    return [min(b[2] for b in boxes),min(b[3] for b in boxes),
            max(b[4] for b in boxes),max(b[5] for b in boxes)]

def _same_box(a,b):
    # Native floating-point accumulation only; never an approximate text search.
    return len(a)==len(b)==4 and all(math.isfinite(v) for v in (*a,*b)) and all(abs(x-y)<=.0001 for x,y in zip(a,b))

def _line_characters(raw,current,bi,li):
    try:line=raw.blocks[bi].lines[li]
    except IndexError as exc:raise ContractError('source readings: unknown occurrence') from exc
    # Geometry identifies the line; codepoints then verify it. Duplicate geometry
    # in either authority is ambiguous, even when the words happen to agree.
    retained=[ln for b in raw.blocks for ln in b.lines if _same_box(ln.bbox,line.bbox)]
    matches=[(b,l,ln) for b,block in enumerate(current.blocks) for l,ln in enumerate(block.lines)
             if _same_box(ln.bbox,line.bbox)]
    need(len(retained)==len(matches)==1,'ambiguous native line geometry')
    cb,cl,native=matches[0]
    need(line.text==native.text,'native line codepoints differ')
    chars=[];offset=0
    for si,span in enumerate(native.spans):
        for char in span.char_boxes:
            chars.append(dict(start=offset+char[0],end=offset+char[1],bbox=list(char[2:]),
                native=dict(block=cb,line=cl,span=si,start=char[0],end=char[1])))
        offset+=len(span.text)
    need(bool(chars) and chars[0]['start']==0 and chars[-1]['end']==len(line.text)
         and all(a['end']==b['start'] for a,b in zip(chars,chars[1:])),'unsupported native character mapping')
    return line,chars


def _inputs(book,doc,source,raw,selectors,synthetic):
    from .source_display import SourceDisplay
    source.validate(book)
    need(json.loads(source.provenance_json).get('layer')=='native','native source required')
    need(not source.report().get('source_readings'),'readings already attached')
    need(type(synthetic) is bool and raw.pno==source.page,'page or evidence mode')
    need(isinstance(selectors,list) and 0<len(selectors)<=64,'occurrence count')
    need(bool(doc.name),'named PDF required')
    page=doc[source.page];frame=list(page.rect)
    need(page.rotation==0 and list(page.cropbox)==list(page.mediabox) and frame[:2]==[0,0]
         and list(page.mediabox)[:2]==[0,0] and [raw.width,raw.height]==frame[2:],'unsupported source frame')
    current=extract.read_page(doc,source.page,keep_char_boxes=True)
    from . import source_inventory
    inventory=book.source_inventory.get(source.page)
    try:source_inventory.validate(inventory,raw)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise ContractError('source readings: retained raw authority differs') from exc
    need(math.ceil(raw.width*1.5)*math.ceil(raw.height*1.5)<=4_000_000,'raster pixel bound')
    display=SourceDisplay(doc,source.page,{'layer':'native'})
    raster=display.jpeg(scale=1.5,quality=85)
    binding=dict(version=VERSION,page=source.page,source_identity=source.identity,
        pdf_sha256=extract.document_fingerprint(doc),raw_sha256=_digest(asdict(raw)),
        inventory_sha256=inventory['sha256'],native_geometry_sha256=_digest(asdict(current)),
        frame=dict(page_rect=frame,rotation=page.rotation,mediabox=list(page.mediabox),cropbox=list(page.cropbox)),
        raster_sha256=sha(raster),synthetic=synthetic,occurrences=[])
    seen={};crops=[]
    for selected in selectors:
        segments=_segments(selected);original=selected['original']
        need(isinstance(original,str) and 0<len(original)<=64 and not any(c.isspace() for c in original),'bounded single token required')
        bi,li=segments[0]['block'],segments[0]['line']
        line,characters=_line_characters(raw,current,bi,li)
        pieces=[];mapping=[];prior=None
        for segment in segments:
            need((segment['block'],segment['line'])==(bi,li),'segments must share one source line')
            si,start,end=(segment[k] for k in ('span','start','end'))
            try:span=line.spans[si]
            except IndexError as exc:raise ContractError('source readings: unknown occurrence') from exc
            need(start<end<=len(span.text),'occurrence range')
            if prior is not None:
                need(si==prior['span']+1 and prior['end']==len(line.spans[prior['span']].text) and start==0,'noncontiguous source segments')
            offset=sum(len(s.text) for s in line.spans[:si])
            whole=[c for c in characters if offset<=c['start'] and c['end']<=offset+len(span.text)]
            need(bool(whole) and whole[0]['start']==offset and whole[-1]['end']==offset+len(span.text),'unsupported retained span mapping')
            need(_same_box(span.bbox,_bbox([(c['start'],c['end'],*c['bbox']) for c in whole])),'retained span geometry differs')
            chosen=[c for c in whole if offset+start<=c['start'] and c['end']<=offset+end]
            need(bool(chosen) and chosen[0]['start']==offset+start and chosen[-1]['end']==offset+end,'unsupported codepoint boundary')
            key=(bi,li,si)
            need(not any(start<hi and lo<end for lo,hi in seen.get(key,[])),'overlapping occurrence')
            seen.setdefault(key,[]).append((start,end))
            pieces.append(span.text[start:end])
            mapping.extend(dict(retained=dict(block=bi,line=li,span=si,start=c['start']-offset,end=c['end']-offset),
                                native=c['native'],bbox=c['bbox']) for c in chosen)
            prior=segment
        need(''.join(pieces)==original,'original codepoints differ')
        first,last=segments[0],segments[-1]
        left=line.spans[first['span']].text;right=line.spans[last['span']].text
        need((first['start']==0 or left[first['start']-1].isspace()) and
             (last['end']==len(right) or right[last['end']].isspace()),'complete span-local token required')
        need(not unicodedata.combining(original[0]) and (last['end']==len(right) or not unicodedata.combining(right[last['end']])),'unsupported codepoint boundary')
        box=_bbox([(0,0,*c['bbox']) for c in mapping])
        need(all(math.isfinite(v) for v in box) and 0<=box[0]<box[2]<=raw.width and 0<=box[1]<box[3]<=raw.height,'invalid character geometry')
        crop=[max(0,box[0]-2),max(0,box[1]-2),min(raw.width,box[2]+2),min(raw.height,box[3]+2)]
        pixels=display.jpeg(crop,scale=3,quality=90);crops.append(pixels)
        occurrence=dict(selected,character_mapping=mapping,char_bbox=box,crop_bbox=crop,crop_sha256=sha(pixels))
        occurrence['id']='reading-'+_digest([binding['source_identity'],binding['raw_sha256'],occurrence])
        binding['occurrences'].append(occurrence)
    return dict(binding_json=encoded(binding),raster=raster,crops=tuple(crops))


def prepare(book,doc,source,raw,selectors,*,synthetic=False):
    from .native_ipc import NativeDocument
    if isinstance(doc,NativeDocument):
        from .layout_native import book_digest
        source.validate(book)
        result=doc.call('readings_prepare',dict(book_digest=book_digest(book),page=source.page,
            source_identity=source.identity,raw_digest=_digest(asdict(raw)),selectors=selectors,synthetic=synthetic))
        need(json.loads(result['binding_json'])['pdf_sha256']==doc.fingerprint,'native PDF binding')
    else:result=_inputs(book,doc,source,raw,selectors,synthetic)
    prepared=PreparedReadings(**result)
    _issued[prepared]=_digest([prepared.binding_json,sha(prepared.raster),[sha(c) for c in prepared.crops]])
    return prepared


def _recognition(prepared,recognition):
    need(_issued.get(prepared)==_digest([prepared.binding_json,sha(prepared.raster),[sha(c) for c in prepared.crops]]),'unissued prepared evidence')
    recognition=json.loads(encoded(recognition));request=prepared.request()
    need(isinstance(recognition,dict) and set(recognition)=={'snapshot','observation','readings'} and recognition['snapshot']==prepared.snapshot,'recognition snapshot')
    need(isinstance(recognition['observation'],str) and 0<len(recognition['observation'])<=128,'recognition observation')
    rows=recognition['readings'];known={o['id']:o for o in request['occurrences']}
    need(isinstance(rows,list) and len(rows)==len(known),'recognition coverage')
    used=set()
    for row in rows:
        need(isinstance(row,dict) and set(row)=={'occurrence','alternatives'} and isinstance(row['occurrence'],str) and row['occurrence'] in known and row['occurrence'] not in used,'recognition occurrence')
        used.add(row['occurrence']);alternatives=row['alternatives']
        need(isinstance(alternatives,list) and len(alternatives)<=3,'alternative bound')
        texts=set()
        for alt in alternatives:
            need(isinstance(alt,dict) and set(alt)=={'text','confidence'},'alternative fields')
            text=alt['text'];confidence=alt['confidence']
            need(isinstance(text,str) and 0<len(text)<=64 and not any(c.isspace() or unicodedata.category(c).startswith('C') for c in text)
                 and text!=known[row['occurrence']]['original'] and text not in texts,'bounded distinct alternative token')
            need(type(confidence) in (int,float) and math.isfinite(confidence) and .9<=confidence<=1,'alternative confidence must be finite and at least .90')
            texts.add(text)
    return recognition


def review_request(prepared,recognition):
    recognition=_recognition(prepared,recognition)
    material=dict(stage='independent-source-reading-review',source=prepared.request(),recognition=recognition,
        instruction='Independently inspect the exact original raster and occurrence crops. Approve only supported alternatives and confidence; otherwise reject. Original text and note associations remain unchanged.')
    return dict(material,snapshot=_digest(material))


def admit(book,doc,source,raw,prepared,recognition,review):
    request=review_request(prepared,recognition)
    review=json.loads(encoded(review))
    need(isinstance(review,dict) and set(review)=={'snapshot','observation','accept','problems'} and review['snapshot']==request['snapshot'],'independent review snapshot')
    need(isinstance(review['observation'],str) and 0<len(review['observation'])<=128 and review['observation']!=recognition['observation'],'distinct independent observation required')
    need(review['accept'] is True and review['problems']==[],'independent review rejected or incomplete')
    binding=prepared.request()
    selectors=[_selector(o) for o in binding['occurrences']]
    current=prepare(book,doc,source,raw,selectors,synthetic=binding['synthetic'])
    need(current.binding_json==prepared.binding_json and current.raster==prepared.raster and current.crops==prepared.crops,'stale source/raster/crop evidence')
    proof=dict(binding=json.loads(current.binding_json),recognition=json.loads(encoded(recognition)),review=review)
    evidence=ReadingEvidence(encoded(proof));_issued[evidence]=evidence.identity()
    return evidence


def attach(book,source,evidence):
    source.validate(book)
    need(type(evidence) is ReadingEvidence and _issued.get(evidence)==evidence.identity(),'unissued reading evidence')
    proof=json.loads(evidence.proof_json)
    need(proof['binding']['source_identity']==source.identity,'reading source changed')
    rows={r['occurrence']:r for r in proof['recognition']['readings']}
    entries=[dict(id=o['id'],original=o['original'],occurrence=o,alternatives=rows[o['id']]['alternatives'],inline_placed=False,
                  synthetic=proof['binding']['synthetic']) for o in proof['binding']['occurrences']]
    report=source.report();report['source_readings']=dict(version=VERSION,proof=proof,entries=entries)
    result=replace(source,report_json=encoded(report),seal='')
    result=replace(result,seal=result._identity());enriched_source._issued[result]=result._identity()
    return result


def replay(book,doc,base,raw,old):
    """Reissue against live authority; serialized evidence is only a replay recipe."""
    record=old.report().get('source_readings')
    if not record:return base
    proof=record['proof'];binding=proof['binding']
    selectors=[_selector(o) for o in binding['occurrences']]
    prepared=prepare(book,doc,base,raw,selectors,synthetic=binding['synthetic'])
    need(json.loads(prepared.binding_json)==binding,'replayed source/raster/crop differs')
    result=attach(book,base,admit(book,doc,base,raw,prepared,proof['recognition'],proof['review']))
    need(result.identity==old.identity and result.html==old.html,'replayed reading source differs')
    return result
