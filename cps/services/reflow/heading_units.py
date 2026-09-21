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
