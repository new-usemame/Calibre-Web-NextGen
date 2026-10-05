"""Closed ruled scan grids, measured without interpreting their cell text.

Deskewing is used only to ask about rule tracks. Packaged source pixels and
the recognition layer are unchanged. Open frames and crossing prose abstain.
"""
import math
from . import extract

SCALE = 3
MAX_PIXELS = 1_000_000


def _tracks(image):
    from PIL import Image
    best = None
    for step in range(-10, 11):
        slope = step * .0025
        straight = image.transform(image.size, Image.Transform.AFFINE,
            (1, 0, 0, slope, 1, -slope * image.width / 2), fillcolor=255)
        data = straight.point(lambda value: 0 if value < 240 else 255).tobytes()
        groups = []
        for y in range(image.height):
            row = data[y * image.width:(y + 1) * image.width]
            if row.count(b'\x00') < .7 * image.width:
                continue
            if groups and y == groups[-1][-1] + 1:
                groups[-1].append(y)
            else:
                groups.append([y])
        groups = [group for group in groups if len(group) <= 3 * SCALE]
        if best is None or len(groups) > len(best[0]):
            best = (groups, slope, straight)
    groups, slope, straight = best
    return [(group[0] + group[-1]) / 2 for group in groups], slope, straight


def measure(doc, pno, rect):
    from PIL import Image
    if (len(rect) != 4 or not all(math.isfinite(v) for v in rect)
            or rect[2] <= rect[0] or rect[3] <= rect[1]
            or (math.ceil((rect[2]-rect[0])*SCALE)+2) *
               (math.ceil((rect[3]-rect[1])*SCALE)+2) > MAX_PIXELS):
        return None
    clip = extract.pymupdf.Rect(rect) & doc[pno].rect
    if clip.is_empty or any(abs(a-b) > .01 for a,b in zip(clip,rect)):
        return None
    pix = doc[pno].get_pixmap(matrix=extract.pymupdf.Matrix(SCALE,SCALE),
        clip=clip, colorspace=extract.pymupdf.csGRAY, alpha=False)
    image = Image.frombytes('L',(pix.width,pix.height),pix.samples)
    horizontal, hslope, hstraight = _tracks(image)
    vertical, vslope, vstraight = _tracks(image.transpose(Image.Transpose.TRANSPOSE))
    if len(horizontal) < 4 or len(vertical) < 3:
        return None
    x0,x1 = vertical[0],vertical[-1]
    y0,y1 = horizontal[0],horizontal[-1]
    if x1-x0 < 30*SCALE or y1-y0 < 30*SCALE:
        return None
    # Every long track reaches both opposite frame edges. This prevents two
    # unrelated ruled objects in the query from being merged into one grid.
    def closes(straight, tracks, lo, hi):
        data=straight.tobytes(); start=max(0,math.floor(lo));end=min(straight.width,math.ceil(hi)+1)
        if end-start < 30*SCALE:return False
        for track in tracks:
            count=0
            for x in range(start,end):
                if any(data[y*straight.width+x] < 240 for y in
                       range(max(0,int(track)-2),min(straight.height,int(track)+3))):
                    count+=1
            if count < .9*(end-start):return False
        return True
    if not closes(hstraight,horizontal,x0,x1) or not closes(vstraight,vertical,y0,y1):
        return None
    # Envelope the actual sloped tracks, including their antialiased edges.
    xpad=abs(vslope)*image.height/2+3*SCALE
    ypad=abs(hslope)*image.width/2+3*SCALE
    return (max(rect[0],pix.x/SCALE+(x0-xpad)/SCALE),
            max(rect[1],pix.y/SCALE+(y0-ypad)/SCALE),
            min(rect[2],pix.x/SCALE+(x1+xpad)/SCALE),
            min(rect[3],pix.y/SCALE+(y1+ypad)/SCALE))


def regions(raw, kept, probe, candidates, notes):
    from .skeleton import Region, _squashed_caption
    if not raw.is_page_scan or probe is None or not hasattr(probe,'grid'):
        return kept,candidates,[],notes
    available=[line for _,group in kept for line in group]
    available.extend(line for note in notes for line in note.lines)
    all_lines=[line for block in raw.text_blocks for line in block.lines]
    captions=[line for line in available if _squashed_caption(line.stripped)]
    owned=set();added=[];remaining=list(candidates)
    for caption in captions:
        em=max(1,caption.size);center=(caption.bbox[0]+caption.bbox[2])/2
        query=(max(0,center-.2*raw.width),max(0,caption.bbox[1]-.45*raw.height),
               min(raw.width,center+.2*raw.width),max(0,caption.bbox[1]-2))
        try:box=probe.grid(query)
        except (ValueError,RuntimeError,AttributeError):continue
        if (not box or box[2]-box[0]<.2*raw.width or box[3]-box[1]<.1*raw.height
                or not 0<=caption.bbox[1]-box[3]<=2*em
                or caption.bbox[0]<box[0] or caption.bbox[2]>box[2]):
            continue
        def inside(b):
            return box[0]<=b[0] and box[1]<=b[1] and b[2]<=box[2] and b[3]<=box[3]
        def touches(b):
            return b[0]<box[2] and box[0]<b[2] and b[1]<box[3] and box[1]<b[3]
        cells=[line for line in all_lines if touches(line.bbox)]
        if (len(cells)<3 or any(not inside(line.bbox) or id(line) in owned for line in cells)
                or any(line not in available for line in cells)
                or any(_squashed_caption(line.stripped) for line in cells)):
            continue
        # A partially overlapping previous figure may own unseen ink outside
        # the grid. Do not replace or trim that unrelated asset.
        if any(touches(c.bbox) and not inside(c.bbox) for c in remaining):continue
        remaining=[c for c in remaining if not touches(c.bbox)]
        owned.update(id(line) for line in cells+[caption])
        added.append(Region(kind='artwork',lines=cells,bbox=box,reason='source_scan_grid'))
        remaining.append(Region(kind='figure',bbox=box,caption_lines=[caption],reason='source_scan_grid'))
    kept=[(block,[line for line in group if id(line) not in owned]) for block,group in kept]
    retained_notes=[]
    from dataclasses import replace
    from .skeleton import _lines_bbox
    for note in notes:
        lines=[line for line in note.lines if id(line) not in owned]
        if lines:retained_notes.append(replace(note,lines=lines,bbox=_lines_bbox(lines,note.bbox)))
    return [(block,group) for block,group in kept if group],remaining,added,retained_notes
