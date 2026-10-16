# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete source display boundaries for quote choices; not semantic approval.

Source coordinates and exact text bind the unit. Sentence punctuation alone is
never a boundary, and typography from OCR is not treated as native evidence.
"""
import re
import statistics
from . import assemble,extract,note_evidence,heading_evidence as geometry

VERSION='source-quote-units-9'


def _normal_positions(text):
    chars=[];positions=[]
    for i,c in enumerate(text):
        if c.isspace():
            if chars and chars[-1]!=' ':chars.append(' ');positions.append(i)
        else:chars.append(c);positions.append(i)
    if chars and chars[-1]==' ':chars.pop();positions.pop()
    return ''.join(chars),positions


def _source_lines(element,lines):
    # Reuse coordinate ownership, but compare source runs rather than the display
    # text accessor: that accessor decorates an atomic note7 as '[7]'.
    mapped,reason=geometry._map(element,lines)
    if reason not in (None,'source_text_mismatch'):return [],[],reason
    text=''.join(str(run[1]) for run in element.runs)
    normal,positions=_normal_positions(text)
    atomic_suffix=[]
    for run in reversed(element.runs):
        if run[0] not in ('sup','mark'):break
        atomic_suffix.insert(0,run)
    atom_length=sum(len(str(run[1])) for run in atomic_suffix)
    uncertain_atom=bool(atomic_suffix) and all('uncertain' in run[2:] for run in atomic_suffix)
    content=text[:-atom_length] if atom_length else text
    content_normal,_=_normal_positions(content)
    def matches(chosen):
        printed='\n'.join(geometry._text(line) for line in chosen)
        for value in (printed,re.sub(r'(?<=\w)-\n(?=[a-z])','',printed)):
            clean=_normal_positions(value)[0]
            if clean==normal:return True
            if uncertain_atom and clean.startswith(content_normal):
                suffix=clean[len(content_normal):].strip()
                if suffix and all(not c.isalnum() for c in suffix):return True
        return False
    if not matches(mapped):
        # Assembly may retain only the first line box or a stale block bbox.
        # Recover a contiguous raw sequence only by exact canonical text, never
        # by fabricating missing lines or treating a near match as source text.
        start=lines.index(mapped[0]) if mapped else -1
        expanded=[]
        for line in lines[start:] if start>=0 else []:
            if expanded:
                previous=expanded[-1]['bbox'];box=line['bbox']
                if not 0<box[1]-previous[1]<=2*max(previous[3]-previous[1],box[3]-box[1]):break
            expanded.append(line)
            if matches(expanded) and all(line in expanded for line in mapped):
                mapped=expanded;break
        else:expanded=[]
        if not matches(mapped):return [],[],'source_text_mismatch'
    # Source line starts must map uniquely and in order into the immutable run.
    ranges=[];cursor=0
    for line in mapped:
        value,_=_normal_positions(geometry._text(line))
        if not value:continue
        # The existing source assembly may join a line-end hyphen. Its presence
        # is not evidence of a second quotation or permission to rewrite words.
        needle=value[:-1] if value.endswith('-') and value not in normal[cursor:] else value
        start=normal.find(needle,cursor)
        if start<0 and uncertain_atom and line is mapped[-1]:
            needle=content_normal[cursor:].strip()
            if not needle or not value.startswith(needle):return [],[],'source_line_mapping_incomplete'
            start=normal.find(needle,cursor)
        if start<0:return [],[],'source_line_mapping_incomplete'
        end=start+len(needle)
        ranges.append([positions[start],positions[end-1]+1]);cursor=end
    return mapped,ranges,None


def _italic(line):
    spans=[s for s in line['spans'] if s.get('text','').strip()]
    return bool(spans) and all(s.get('flags',0)&extract.FLAG_ITALIC or
        'italic' in s.get('font','').lower() or 'oblique' in s.get('font','').lower() for s in spans)


def source_fragment_continues(left, right, raw_page, elements):
    """A native delimited display split at ordinary printed line leading.

    This joins existing paragraph fragments, never assigns quote semantics or
    clears uncertainty. Missing source ownership and OCR typography abstain.
    """
    if (raw_page is None or left.kind!='p' or right.kind!='p'
            or left.table_row or right.table_row or left.column!=right.column
            or left.pno!=right.pno or left.pno!=raw_page.pno
            or getattr(raw_page,'transcript_unverified',False)
            or left.display_group or right.display_group
            or any(run[0]=='glyph' for element in (left,right) for run in element.runs)):
        return False
    a=''.join(str(run[1]) for run in left.runs).strip()
    b=''.join(str(run[1]) for run in right.runs).strip()
    for opening,closing in [('"','"'),('“','”')]:
        if not a.startswith(opening):continue
        if opening==closing:
            complete=a.count(opening)==1 and b.count(closing)==1
        else:
            complete=(a.count(opening)==1 and closing not in a
                      and opening not in b and b.count(closing)==1)
        if complete and re.fullmatch(r'[\s.,;:!?\d]*',b.split(closing)[-1]):break
    else:return False
    if assemble.SENT_END.search(a) or not a[-1:].isalnum():return False
    raw=raw_page.to_dict()
    lines=[line for block in raw['blocks'] if block.get('kind','text')=='text'
           for line in block.get('lines',[]) if geometry._valid(line.get('bbox')) and line.get('spans')]
    first,_,error=_source_lines(left,lines)
    if error or not first:return False
    following,_,error=_source_lines(right,lines)
    if error or not following:return False
    chosen=first+following
    spans=geometry._spans(chosen)
    if (not spans or any(span.get('font')=='ocr' for span in spans)
            or any(line in first for line in following)):
        return False
    containing=[block for block in raw['blocks'] if any(line in chosen for line in block.get('lines',[]))]
    if any(any(line not in chosen for line in block.get('lines',[])) for block in containing):return False
    sizes=[span['size'] for span in spans if span.get('text','').strip() and span.get('size',0)>0]
    if not sizes:return False
    em=statistics.median(sizes)
    boxes=[line['bbox'] for line in chosen]
    left_edge=min(box[0] for box in boxes)
    if any(abs(box[0]-left_edge)>.5*em for box in boxes):return False
    heights=[box[3]-box[1] for box in boxes]
    steps=[y[1]-x[1] for x,y in zip(boxes,boxes[1:])]
    if (not steps or min(steps)<=0 or max(steps)>1.7*statistics.median(heights)
            or any(y[1]<x[3]-.25*em for x,y in zip(boxes,boxes[1:]))):return False
    # A nearby wider body paragraph establishes this as a distinct inset
    # display, rather than two coincidentally adjacent ordinary paragraphs.
    for reference in elements:
        if (reference is left or reference is right or reference.kind!='p'
                or reference.table_row or reference.column!=left.column):continue
        mapped,_,error=_source_lines(reference,lines)
        if error or len(mapped)<1:continue
        refbox=reference.bbox
        if not geometry._valid(refbox) or refbox[0]+em>left_edge or refbox[2]<=left_edge+em:continue
        gap=min(abs(boxes[0][1]-refbox[3]),abs(refbox[1]-boxes[-1][3]))
        if (refbox[3]<=boxes[0][1] or refbox[1]>=boxes[-1][3]) and .5*em<=gap<=5*em:return True
    return False


def _same_page_continuation(elements,index,mapped,lines):
    """Element and PDF-block boundaries can split one continuous display.

    Adjacent same-column paragraphs at the same indent and ordinary line spacing
    do not establish separate complete units, even if punctuation is uncertain
    or the extraction engine created separate raw blocks. This adapter cannot
    wrap across elements, so both fragments must abstain.
    """
    element=elements[index]
    for neighbor_index in (index-1,index+1):
        if not 0<=neighbor_index<len(elements):continue
        neighbor=elements[neighbor_index]
        if neighbor.kind!='p' or neighbor.table_row or neighbor.column!=element.column:continue
        adjacent,_,error=_source_lines(neighbor,lines)
        if error:adjacent,_=geometry._map(neighbor,lines)
        if not adjacent:continue
        prior,following=(adjacent[-1],mapped[0]) if neighbor_index<index else (mapped[-1],adjacent[0])
        a,b=prior['bbox'],following['bbox']
        em=max(a[3]-a[1],b[3]-b[1])
        aligned=(abs(a[0]-b[0])<=em*.5 or abs((a[0]+a[2]-b[0]-b[2])/2)<=em*.5)
        if 0<b[1]-a[1]<=em*1.7 and aligned:
            return True
    return False


def _cross_page_continuation(book,pno,index,mapped):
    """Bounded neighboring source context outranks incidental element order.

    A citation/footer may follow the last display, or a running header/figure
    may precede its tail. Match the display's inset relative to each page's body
    column, allowing a first-line indent, then test conservative boundary uncertainty. This never
    merges or rewrites the two fragments; uncertain complete boundaries abstain.
    """
    element=book.pages[pno][index]
    em=statistics.median(line['bbox'][3]-line['bbox'][1] for line in mapped)
    def column(page):
        return [e for e in book.pages.get(page,[]) if e.kind=='p' and not e.table_row
                and e.column==element.column and geometry._valid(e.bbox)]
    current=column(pno)
    if not current:return False
    left=min(e.bbox[0] for e in current)
    inset=min(line['bbox'][0] for line in mapped)-left
    if inset<=em*.5:return False
    def edge(page,last):
        paragraphs=column(page)
        if not paragraphs:return None
        body_left=min(e.bbox[0] for e in paragraphs)
        def aligned(e):
            offset=e.bbox[0]-body_left-inset
            if abs(offset)<=em*.5:return True
            # Only an unfinished one-line boundary can carry additional first-line
            # indentation. A complete citation does not replace the display edge.
            return (0<offset<=em*2.5 and len(e.line_boxes)==1 and
                    (note_evidence.may_continue(e.text,'continuation') if last else
                     note_evidence.may_continue('unfinished',e.text)))
        display=[e for e in paragraphs if aligned(e) and not re.fullmatch(r'\d{1,4}',e.text.strip())]
        return (display[-1] if last else display[0]) if display else None
    if edge(pno,True) is element:
        following=edge(pno+1,False)
        if following is not None and note_evidence.may_continue(element.text,following.text):return True
        # A one-line paragraph opening can finish on the next page at the body
        # margin: its first-line indent is not a separate displayed quotation.
        if len(mapped)==1:
            neighbors=column(pno+1)
            if neighbors:
                margin=min(e.bbox[0] for e in neighbors)
                body=[e for e in neighbors if abs(e.bbox[0]-margin)<=em*.5 and not re.fullmatch(r'\d{1,4}',e.text.strip())]
                if body and note_evidence.may_continue(element.text,body[0].text):return True
    if edge(pno,False) is element:
        previous=edge(pno-1,True)
        if previous is not None and note_evidence.may_continue(previous.text,element.text):return True
    return False


def quote_evidence(book,pno,raw_page,layer,source_rotation=0,uncertain=False):
    raw=raw_page.to_dict() if hasattr(raw_page,'to_dict') else raw_page
    lines=[line for block in (raw or {}).get('blocks',[]) if block.get('kind','text')=='text'
           for line in block.get('lines',[]) if geometry._valid(line.get('bbox')) and line.get('spans')]
    result={};elements=book.pages[pno]
    for index,element in enumerate(elements):
        proof={'version':VERSION,'supported':False,'reason':'missing_source_geometry','units':[]}
        result[index]=proof
        if any(run[0]=="glyph" and (not isinstance(run[2],dict) or run[2].get("reason")!="transcript") for run in element.runs):
            proof["reason"]="unmapped_native_glyphs";continue
        if raw is None or raw.get('pno')!=pno or layer not in ('native','ocr') or source_rotation:
            continue
        if element.kind!='p' or element.table_row:
            proof['reason']='unsupported_or_uncertain_source';continue
        if re.fullmatch(r'\d{1,4}',element.text.strip()):
            proof['reason']='numeric_furniture_not_quote';continue
        mapped,ranges,error=_source_lines(element,lines)
        if error:proof['reason']=error;continue
        proof['source_line_boxes']=[line['bbox'] for line in mapped]
        proof['source_line_ranges']=ranges
        if not mapped or len(mapped)!=len(ranges):continue
        containing=[block for block in raw.get('blocks',[]) if any(line in mapped for line in block.get('lines',[]))]
        if any(any(line not in mapped for line in block.get('lines',[])) for block in containing):
            proof['reason']='incomplete_source_display_unit';continue
        if (geometry._continued(book,pno,index) or
            _same_page_continuation(elements,index,mapped,lines) or
            _cross_page_continuation(book,pno,index,mapped)):
            proof['reason']='known_source_continuation';continue
        text=''.join(str(run[1]) for run in element.runs)
        end=len(text)
        for run in reversed(element.runs):
            if run[0] not in ('sup','mark'):break
            end-=len(str(run[1]))
        while end and text[end-1].isspace():end-=1
        start=len(text)-len(text.lstrip())
        if start>=end:continue
        heights=[line['bbox'][3]-line['bbox'][1] for line in mapped]
        em=statistics.median(heights)
        left=min(line['bbox'][0] for line in mapped)
        # Every line must remain inset from the body column below. A display
        # paragraph may additionally indent its first line; ordinary first-line
        # indentation returns to the body column and fails that minimum inset.
        centers=[(line['bbox'][0]+line['bbox'][2])/2 for line in mapped]
        centered=(max(line['bbox'][0] for line in mapped)-left>em*.5 and
                  max(centers)-min(centers)<=em*.5)
        references=[other for other in elements if other is not element and other.kind=='p'
            and not other.table_row and other.column==element.column and geometry._valid(other.bbox)
            and other.bbox[0]+em*(.5 if centered else 1)<=left and other.bbox[2]>left+em
            and (not centered or abs((other.bbox[0]+other.bbox[2])/2-statistics.median(centers))<=em*.5)
            and (other.bbox[3]<=element.bbox[1] or other.bbox[1]>=element.bbox[3])]
        if not references:proof['reason']='no_source_display_indent';continue
        nearest=min(references,key=lambda other:min(abs(element.bbox[1]-other.bbox[3]),abs(other.bbox[1]-element.bbox[3])))
        gap=min(abs(element.bbox[1]-nearest.bbox[3]),abs(nearest.bbox[1]-element.bbox[3]))
        if gap<em*.5:proof['reason']='no_display_separation';continue
        if len(mapped)>1:
            steps=[b['bbox'][1]-a['bbox'][1] for a,b in zip(mapped,mapped[1:])]
            if min(steps)<=0 or max(steps)>statistics.median(steps)*1.8:
                proof['reason']='multiple_source_display_units';continue
        content_end=end
        atomic_end=len(text)
        # A distinct trailing parenthetical in contrasting native italics is
        # annotation, not part of the preceding display. Require whole mapped
        # source lines and one balanced parenthetical; never split an inline
        # aside or a consistently italic quotation. OCR/uncertain punctuation
        # cannot establish this boundary and must abstain for this mixed unit.
        annotation=False
        split=len(mapped)
        while split and _italic(mapped[split-1]):split-=1
        if 0<split<len(mapped) and not any(_italic(line) for line in mapped[:split]):
            suffix=text[ranges[split][0]:end].strip()
            depth=0;balanced=suffix.startswith('(') and suffix.endswith(')')
            for i,char in enumerate(suffix):
                if char=='(':depth+=1
                elif char==')':depth-=1
                if depth<0 or (depth==0 and i<len(suffix)-1):balanced=False
            if balanced and depth==0:
                if layer!='native' or element.punctuation_uncertain or uncertain:
                    proof['reason']='uncertain_annotation_boundary';continue
                end=ranges[split-1][1];annotation=True
        body=text[start:end]
        # A complete outer quoted range can be separated from an attribution only
        # at actual source line boundaries, with native contrasting typography.
        # Inline substrings and inferred OCR italics cannot establish that split.
        pairs=[('"','"'),('“','”'),('‘','’')]
        delimited=[]
        boundary_uncertain=element.punctuation_uncertain or uncertain
        proof['retained_uncertainty']=bool(boundary_uncertain)
        # A terminal transcription-image word is preserved as a whole source
        # atom. Its unreliable logical punctuation cannot establish an internal
        # quotation split. Complete layout proof above still governs the unit.
        cursor=0;terminal_image=None
        for run in element.runs:
            following=cursor+len(str(run[1]))
            if run[0]=='glyph' and isinstance(run[2],dict) and run[2].get('reason')=='transcript' and following==end:
                terminal_image=(cursor,following)
            cursor=following
        ambiguous_marks=[start+i for i,char in enumerate(body) if char in ('"','“','”')]
        protected_terminal=bool(terminal_image and ambiguous_marks and
            all(terminal_image[0]<=position<terminal_image[1] for position in ambiguous_marks))
        if boundary_uncertain and ambiguous_marks and not body.startswith(('"','“')) and not protected_terminal:
            proof['reason']='uncertain_mixed_quote_boundary';continue
        for opening,closing in (() if boundary_uncertain else pairs):
            a=body.find(opening);b=body.rfind(closing)
            if 0<=a<b:delimited.append((start+a,start+b+1))
        if delimited:
            qstart,qend=min(delimited,key=lambda bounds:bounds[0])
            first=next((i for i,(a,b) in enumerate(ranges) if a==qstart),None)
            last=next((i for i,(a,b) in enumerate(ranges) if a<qend<=b),None)
            outside=[i for i,(a,b) in enumerate(ranges) if b<=qstart or a>=qend]
            if (first is None or last is None or
                any(text[qend:min(b,end)].strip() for a,b in ranges if a<qend<b) or
                (outside and (layer!='native' or not all(_italic(mapped[i]) for i in outside)
                              or all(_italic(line) for line in mapped[first:last+1])))):
                proof['reason']='mixed_or_inline_quotation';continue
            start,end=qstart,qend
        elif (not boundary_uncertain and any(mark in body for mark in ('"','“','”'))
              and not (body.endswith(('"','”')) and not body.startswith(('"','“')))):
            proof['reason']='unbalanced_quote_boundary';continue
        # Every retained range is a complete layout unit. A model must still
        # decide whether the display has quotation semantics rather than assume
        # that indentation alone establishes authorship.
        if end==content_end:end=atomic_end
        proof.update(supported=True,reason='complete_source_display_unit',units=[{
            'source_range':[start,end],'text':text[start:end],
            'source_line_boxes':[line['bbox'] for line,(a,b) in zip(mapped,ranges) if a<end and b>start],
            'boundary':'delimited_source_lines' if delimited else 'annotation_source_lines' if annotation else 'complete_display_paragraph'}])
    return result
