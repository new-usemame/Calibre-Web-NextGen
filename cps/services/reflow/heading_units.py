"""Conservative native source-line units, independent of PDF block ownership."""
import math
import statistics

VERSION='native-visual-units-1'
PRECISION=.02
ALIGNMENT_EM=.5
LINE_GAP_EM=.5
BASELINE_STEP_EM=1.5


def _shape(line):
    box=line.get('bbox',())
    if len(box)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in box):return None
    if box[2]<=box[0] or box[3]<=box[1]:return None
    spans=[s for s in line.get('spans',[]) if s.get('text','').strip() and not s.get('flags',0)&1]
    if not spans:return None
    sizes=[s.get('size',0) for s in spans]
    if any(not isinstance(s,(int,float)) or not math.isfinite(s) or s<=0 for s in sizes):return None
    size=statistics.median(sizes)
    if any(abs(s-size)>PRECISION for s in sizes):return None
    faces={(s.get('font',''),s.get('flags',0)) for s in spans}
    if len(faces)!=1:return None
    baseline=statistics.median(s.get('origin_y',box[3]) for s in spans)
    return box,size,next(iter(faces)),baseline


def native_units(lines):
    """Indices of coherent adjacent lines; preserve every input line exactly once.

    Same face/size and coherent alignment are necessary. Neither extracted
    semantic kind nor text vocabulary is evidence for membership. Ambiguous
    branching, intervening text and overlapping columns do not establish a join.
    """
    shapes=[_shape(line) for line in lines];edges=[]
    for i,a in enumerate(shapes):
        if a is None:continue
        ab,size,face,ay=a
        for j,b in enumerate(shapes):
            if b is None or j==i:continue
            bb,bs,bface,by=b
            if face!=bface or abs(size-bs)>PRECISION or not 0<by-ay<=BASELINE_STEP_EM*size:continue
            if bb[1]<ab[1] or bb[1]-ab[3]>LINE_GAP_EM*size:continue
            overlap=min(ab[2],bb[2])-max(ab[0],bb[0])
            if overlap<=0:continue
            centered=abs((ab[0]+ab[2]-bb[0]-bb[2])/2)<=ALIGNMENT_EM*size
            left=abs(ab[0]-bb[0])<=ALIGNMENT_EM*size
            if not (centered or left):continue
            if any(k not in (i,j) and c is not None and ay<c[3]<by and
                   min(ab[2],bb[2],c[0][2])>max(ab[0],bb[0],c[0][0])
                   for k,c in enumerate(shapes)):continue
            edges.append((i,j))
    # A branching geometry is unresolved; never choose a source line by order.
    forward={i:[j for a,j in edges if a==i] for i in range(len(lines))}
    backward={j:[i for i,b in edges if b==j] for j in range(len(lines))}
    next_line={i:js[0] for i,js in forward.items() if len(js)==1 and len(backward[js[0]])==1}
    previous=set(next_line.values());groups=[];seen=set()
    for start in range(len(lines)):
        if start in previous:continue
        group=[];index=start
        while index not in seen:
            seen.add(index);group.append(index)
            if index not in next_line:break
            index=next_line[index]
        groups.append(group)
    groups.extend([[i] for i in range(len(lines)) if i not in seen])
    return groups


def initial_spacing(doc,pno,initial,following):
    """Compare the native initial gap with actual printed spaces in that face."""
    if doc is None:return {}
    if initial.text!=initial.text.strip() or following.text!=following.text.lstrip():return {}
    if not initial.stripped.isalpha() or len(initial.stripped)!=1:return {}
    word=following.stripped.split()[0]
    if not word.isalpha():return {}
    face=following.spans[0];gap=following.bbox[0]-initial.bbox[2]
    widths=[]
    for block in doc[pno].get_text('rawdict').get('blocks',[]):
        for line in block.get('lines',[]):
            for span in line.get('spans',[]):
                if span.get('font')!=face.font or abs(span.get('size',0)-face.size)>PRECISION:continue
                widths.extend(char['bbox'][2]-char['bbox'][0] for char in span.get('chars',[])
                              if char.get('c')==' ')
    widths=[w for w in widths if math.isfinite(w) and w>0]
    if not widths or max(widths)-min(widths)>PRECISION:return {}
    if not 0<=gap<min(widths)-PRECISION:return {}
    return {'version':'native-initial-spacing-1','initial':initial.stripped,
            'following_word':word,'initial_bbox':list(initial.bbox),
            'following_bbox':list(following.bbox),'gap':gap,
            'font':face.font,'size':face.size,'space_width':statistics.median(widths),
            'observed_spaces':len(widths)}

# A separate geometric contract for a page-opening display group. Font families
# are not proof on fitted OCR text; line extents and whitespace carry this rule.
OPENING_VERSION='opening-display-unit-1'
OPENING_ANCHOR_RATIO=1.5
OPENING_GAP_EM=1.5
OPENING_BODY_SEPARATION_EM=2.0


def opening_display_unit(lines,body_size,width,height,scan=False):
    from . import skeleton
    if not body_size or body_size<=0:return {}
    measured=[]
    for i,line in enumerate(lines):
        box=line.get('bbox',())
        spans=[s for s in line.get('spans',[]) if s.get('text','').strip()]
        if len(box)!=4 or not all(math.isfinite(v) for v in box) or box[2]<=box[0] or box[3]<=box[1]:return {}
        if not spans:continue
        sizes=[s.get('size',0) for s in spans]
        if any(not math.isfinite(v) or v<=0 for v in sizes):return {}
        if box[3]<=height*skeleton.HEADER_BAND or box[1]>=height*skeleton.FOOTER_BAND:continue
        measured.append((i,box,statistics.median(sizes),spans))
    measured.sort(key=lambda item:(item[1][1],item[1][0]))
    broad=[x for x in measured if x[1][2]-x[1][0]>width*.35]
    if len(broad)<3:return {}
    body_height=statistics.median(x[1][3]-x[1][1] for x in broad)
    group=[measured[0]];alignment={'left','center'}
    for item in measured[1:]:
        previous=group[-1];ab,bb=previous[1],item[1]
        size=max(x[1][3]-x[1][1] for x in group+[item])
        gap=bb[1]-ab[3]
        if bb[1]<=ab[1] or gap>OPENING_GAP_EM*size:break
        aligned=set()
        if abs(bb[0]-group[0][1][0])<=ALIGNMENT_EM*body_size:aligned.add('left')
        if abs((bb[0]+bb[2]-group[0][1][0]-group[0][1][2])/2)<=ALIGNMENT_EM*body_size:aligned.add('center')
        alignment &= aligned
        if not alignment:break
        group.append(item)
    if len(group)<2 or len(group)==len(measured):return {}
    # Require a visibly prominent, wide title line, not a display initial.
    anchors=[x for x in group if x[1][3]-x[1][1]>=body_height*OPENING_ANCHOR_RATIO and x[1][2]-x[1][0]>2*(x[1][3]-x[1][1])]
    if not anchors:return {}
    anchor=max(anchors,key=lambda x:x[1][3]-x[1][1])
    following=measured[len(group):]
    gap=following[0][1][1]-max(x[1][3] for x in group)
    internal=max(b[1][1]-a[1][3] for a,b in zip(group,group[1:]))
    if gap<OPENING_BODY_SEPARATION_EM*body_size or gap<=internal:return {}
    body=[x for x in following if x[1][2]-x[1][0]>width*.35]
    if len(body)<2:return {}
    left=statistics.median(x[1][0] for x in body);right=statistics.median(x[1][2] for x in body)
    if 'left' in alignment and abs(group[0][1][0]-left)>ALIGNMENT_EM*body_size:alignment.discard('left')
    if 'center' in alignment and abs((group[0][1][0]+group[0][1][2]-left-right)/2)>ALIGNMENT_EM*body_size:alignment.discard('center')
    if not alignment:return {}
    for _,box,_,spans in group:
        text=''.join(s.get('text','') for s in spans).strip()
        if skeleton.CAPTION_LINE.match(text) or text.endswith(('-', '\u2010')):return {}
        if any(s.get('flags',0)&1 for s in spans):return {}
    fitted=scan or any('glyphless' in s.get('font','').lower() for x in group for s in x[3])
    return {'version':OPENING_VERSION,'indices':[x[0] for x in group],
        'anchor_index':anchor[0],'alignment':'center' if 'center' in alignment else 'left',
        'line_boxes':[list(x[1]) for x in group],
        'relative_sizes':[round((x[1][3]-x[1][1])/(anchor[1][3]-anchor[1][1]) if fitted else x[2]/anchor[2],3) for x in group],
        'typography':'fitted_geometry' if fitted else 'native_spans',
        'body_line_height':body_height,'body_separation':gap,'maximum_internal_gap':internal}
