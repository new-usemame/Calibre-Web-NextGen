"""Source-role admission for headings; typography is necessary, not semantics."""
import math
import re
import statistics
import unicodedata
from collections import Counter

from . import assemble,extract,skeleton

VERSION='source-heading-evidence-1'
BOX_TOLERANCE=.02  # two units of serialized source-coordinate precision
ALIGNMENT_EM=.5
ISOLATION_LEADING=.5
GRAPHIC_GAP_EM=1.5
BODY_LEADING_TOLERANCE=.25


def _normal(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFC',text)).strip()


def _text(line):return ''.join(s.get('text','') for s in line['spans'])
def _spans(lines):return [s for line in lines for s in line['spans']
    if s.get('text','').strip() and not s.get('flags',0)&extract.FLAG_SUPERSCRIPT]
def _bold(span):return bool(span.get('flags',0)&extract.FLAG_BOLD) or extract._is_bold_font(span.get('font',''))
def _baseline(line):return statistics.median(s.get('origin_y',line['bbox'][3]) for s in line['spans'])


def _valid(box):
    return isinstance(box,(list,tuple)) and len(box)==4 and all(
        isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in box
    ) and box[2]>box[0] and box[3]>box[1]


def _inside(inner,outer):
    return inner[0]>=outer[0]-BOX_TOLERANCE and inner[1]>=outer[1]-BOX_TOLERANCE and inner[2]<=outer[2]+BOX_TOLERANCE and inner[3]<=outer[3]+BOX_TOLERANCE


def _map(element,lines):
    if not _valid(element.bbox):return [],'missing_source_geometry'
    selected=[]
    if element.line_boxes:
        for box in element.line_boxes:
            if not _valid(box):return selected,'invalid_source_geometry'
            matches=[line for line in lines if all(abs(a-b)<=BOX_TOLERANCE for a,b in zip(line['bbox'],box))]
            if len(matches)!=1 or matches[0] in selected:return selected,'ambiguous_source_mapping'
            selected.append(matches[0])
    else:
        selected=[line for line in lines if _inside(line['bbox'],element.bbox)]
        selected.sort(key=lambda line:(line['bbox'][1],line['bbox'][0]))
    if not selected:return [],'missing_source_geometry'
    printed='\n'.join(_text(line) for line in selected)
    # Only the existing line-end hyphen join is tolerated; no punctuation,
    # bracket, quote or vocabulary substitution establishes source mapping.
    variants={_normal(printed),_normal(re.sub(r'(?<=\w)-\n(?=[a-z])','',printed))}
    if _normal(element.text) not in variants:return selected,'source_text_mismatch'
    return selected,None


def _continued(book,pno,index):
    elements=book.pages[pno]
    if index==0:
        previous=book.pages.get(pno-1,[])
        if previous and previous[-1].kind=='p' and assemble.continues(previous[-1].text,elements[index].text):return True
    if index==len(elements)-1:
        following=book.pages.get(pno+1,[])
        if following and following[0].kind=='p' and assemble.continues(elements[index].text,following[0].text):return True
    return len(set(elements[index].pages or [pno]))>1


def heading_evidence(book,pno,raw_page,layer,source_rotation=0,reading_size=None):
    raw=raw_page.to_dict() if hasattr(raw_page,'to_dict') else raw_page
    elements=book.pages[pno];result={}
    lines=[line for block in (raw or {}).get('blocks',[]) if block.get('kind','text')=='text'
           for line in block.get('lines',[]) if _valid(line['bbox']) and line.get('spans')]
    for index,element in enumerate(elements):
        proof={'version':VERSION,'supported':False,'reason':'missing_source_geometry','source_line_boxes':[]}
        result[index]=proof
        if raw is None or raw.get('pno')!=pno or layer not in ('native','ocr'):continue
        # Native rotated pages retain unrotated spans but displayed dimensions
        # in RawPage. Do not claim column/margin evidence in a mixed frame.
        # Recovery's chosen lines already use the corrected reading frame.
        if layer=='native' and source_rotation:
            proof['reason']='unsupported_source_coordinate_frame';continue
        if reading_size and any(not isinstance(raw.get(key),(int,float)) or
                not math.isfinite(raw[key]) or abs(raw[key]-size)>BOX_TOLERANCE
                for key,size in zip(('width','height'),reading_size)):
            proof['reason']='source_frame_mismatch';continue
        mapped,error=_map(element,lines)
        proof['source_line_boxes']=[line['bbox'] for line in mapped]
        if error:proof['reason']=error;continue
        box=element.bbox
        if _continued(book,pno,index):proof['reason']='known_source_continuation';continue
        if box[3]<=raw['height']*skeleton.HEADER_BAND or box[1]>=raw['height']*skeleton.FOOTER_BAND:
            proof['reason']='source_margin_unit';continue
        others=[line for line in lines if line not in mapped]
        spans=_spans(mapped)
        body_size=getattr(getattr(book,'style',None),'body_size',0)
        if not body_size:
            census=Counter()
            for line in others:
                if line['bbox'][3]<=raw['height']*skeleton.HEADER_BAND or line['bbox'][1]>=raw['height']*skeleton.FN_ZONE_NUMBERED:continue
                for span in _spans([line]):census[round(span['size'],1)]+=len(span['text'].strip())
            body_size=census.most_common(1)[0][0] if census else 0
        if not body_size or not math.isfinite(body_size) or not spans:
            proof['reason']='missing_body_reference';continue
        same_size=lambda span:abs(span['size']/body_size-1)<=skeleton.LADDER_TOL
        body_lines=[line for line in others if _spans([line]) and all(same_size(span) for span in _spans([line]))]
        center=(box[0]+box[2])/2
        column=[line for line in body_lines if line['bbox'][0]-body_size<=center<=line['bbox'][2]+body_size]
        if not column:proof['reason']='missing_body_reference';continue
        # Use neighboring source blocks in this column. A fixed number of
        # nearest lines can silently borrow another paragraph/column's style.
        blocks=[[line for line in block.get('lines',[]) if line in column]
                for block in raw.get('blocks',[])]
        blocks=[block for block in blocks if block]
        prior=[block for block in blocks if max(line['bbox'][3] for line in block)<=box[1]+BOX_TOLERANCE]
        later=[block for block in blocks if min(line['bbox'][1] for line in block)>=box[3]-BOX_TOLERANCE]
        adjacent=[block for block in blocks if block not in prior and block not in later]
        if prior:adjacent.append(max(prior,key=lambda block:max(line['bbox'][3] for line in block)))
        if later:adjacent.append(min(later,key=lambda block:min(line['bbox'][1] for line in block)))
        nearest=[line for block in adjacent for line in block]
        if not nearest:proof['reason']='missing_body_reference';continue
        left=statistics.median(line['bbox'][0] for line in nearest)
        right=max(line['bbox'][2] for line in nearest)
        baselines=sorted(_baseline(line) for line in nearest)
        steps=[b-a for a,b in zip(baselines,baselines[1:]) if 0<b-a<=body_size*2]
        leading=statistics.median(steps) if steps else body_size*1.2
        above=[line for line in column if line['bbox'][3]<=box[1]+BOX_TOLERANCE]
        below=[line for line in column if line['bbox'][1]>=box[3]-BOX_TOLERANCE]
        before=box[1]-max(line['bbox'][3] for line in above) if above else box[1]
        after=min(line['bbox'][1] for line in below)-box[3] if below else raw['height']-box[3]
        proof.update(body_size=round(body_size,3),body_leading=round(leading,3),
            column_bounds=[round(left,3),round(right,3)],gap_before=round(before,3),gap_after=round(after,3))
        # Native figures use the same source frame as native spans. OCR picture
        # frames are not silently mixed with corrected reading coordinates.
        if layer=='native':
            graphic=False
            for figure in book.figures:
                if figure['pno']!=pno or not _valid(figure['bbox']):continue
                f=figure['bbox'];overlap=min(box[2],f[2])-max(box[0],f[0])
                distance=max(f[1]-box[3],box[1]-f[3],0)
                if overlap>0 and distance<=GRAPHIC_GAP_EM*body_size:graphic=True;break
            if graphic:proof['reason']='source_graphic_association';continue
        body_regular=not all(_bold(span) for span in _spans(nearest))
        native_contrast=layer=='native' and all(
            span['size']>=body_size*skeleton.HEAD_RATIO or
            (_bold(span) and body_regular and span['size']>=body_size*(1-skeleton.LADDER_TOL)) for span in spans)
        normal=all(same_size(span) and (layer!='native' or not _bold(span)) for span in spans)
        candidate_steps=[_baseline(b)-_baseline(a) for a,b in zip(mapped,mapped[1:])]
        indented=len(mapped)>1 and mapped[0]['bbox'][0]>statistics.median(line['bbox'][0] for line in mapped[1:])+6
        same_leading=candidate_steps and all(abs(step/leading-1)<=BODY_LEADING_TOLERANCE for step in candidate_steps)
        if indented and normal and same_leading and min(before,after)<=leading:
            proof['reason']='ordinary_source_paragraph';continue
        isolated=min(before,after)>=ISOLATION_LEADING*leading
        aligned=all(abs((line['bbox'][0]+line['bbox'][2])/2-(left+right)/2)<=ALIGNMENT_EM*body_size
                    and line['bbox'][0]>=left+ALIGNMENT_EM*body_size
                    and line['bbox'][2]<=right-ALIGNMENT_EM*body_size for line in mapped)
        containing=[block for block in raw.get('blocks',[]) if any(line in mapped for line in block.get('lines',[]))]
        complete_raw_unit=bool(containing) and all(all(line in mapped for line in block.get('lines',[])) for block in containing)
        proof['complete_raw_unit']=complete_raw_unit
        if native_contrast and (isolated or complete_raw_unit):
            proof.update(supported=True,reason='whole_unit_native_typography')
        elif aligned and isolated:
            proof.update(supported=True,reason='centered_isolated_source_unit')
        else:proof['reason']='no_standalone_display_evidence'
    return result
