"""Bounded original pixels in the source's established upright reading space."""
import math
import hashlib
import json
from . import extract, ocr

VERSION = 'source-display-1'
MAX_INSPECTION_TILES = 64
GRID_QUERY_PIXELS = 1_000_000


def _rect(value):
    values=tuple(value)
    if len(values)!=4 or not all(math.isfinite(v) for v in values):
        raise ValueError('invalid source display geometry')
    rect=extract.pymupdf.Rect(values)
    if rect.is_empty or rect.is_infinite:raise ValueError('invalid source display geometry')
    return rect


class SourceDisplay:
    def __init__(self,doc,pno,provenance=None):
        self.doc,self.pno=doc,pno
        self.page=doc[pno];self.provenance=provenance or {}
        self.angle=self.provenance.get('orientation',0) if self.provenance.get('layer')=='ocr' else 0
        if self.angle not in (0,90,180,270):raise ValueError('invalid source orientation')
        self.displayed=_rect(self.page.rect)
        if self.provenance.get('layer')=='ocr':
            if self.provenance.get('source_rotation',self.page.rotation)!=self.page.rotation:
                raise ValueError('stale source rotation')
            saved=self.provenance.get('page_rect')
            if saved and any(abs(a-b)>.01 for a,b in zip(saved,self.displayed)):
                raise ValueError('stale source rectangle')
        w,h=self.displayed.width,self.displayed.height
        self.rect=extract.pymupdf.Rect(0,0,h,w) if self.angle in (90,270) else extract.pymupdf.Rect(0,0,w,h)

    def source_rect(self,reading_rect):
        rect=_rect(reading_rect)&self.rect
        if rect.is_empty:raise ValueError('source region outside page')
        # get_pixmap clips in the displayed PDF space, unlike text/figure boxes
        # that may be stored in unrotated PDF coordinates.
        return extract.pymupdf.Rect(ocr._source_box(rect,self.displayed.width,self.displayed.height,self.angle))

    def reading_rect(self,unrotated_rect):
        source=_rect(unrotated_rect)*self.page.rotation_matrix
        rotated=source*extract.pymupdf.Matrix(1,1).prerotate(self.angle)
        origin=self.displayed*extract.pymupdf.Matrix(1,1).prerotate(self.angle)
        return extract.pymupdf.Rect(rotated.x0-origin.x0,rotated.y0-origin.y0,
                                    rotated.x1-origin.x0,rotated.y1-origin.y0)&self.rect

    def pixmap(self,rect=None,scale=1,gray=False,max_pixels=None):
        reading=self.rect if rect is None else _rect(rect)&self.rect
        if reading.is_empty:raise ValueError('source region outside page')
        if not math.isfinite(scale) or scale<=0:raise ValueError('invalid source scale')
        limit=min(extract.MAX_RASTER_PIXELS,max_pixels or extract.MAX_RASTER_PIXELS)
        if not math.isfinite(limit) or limit < 16:raise ValueError('invalid source pixel bound')
        bounded=min(float(scale),math.sqrt(limit/(reading.width*reading.height)))
        if (math.ceil(reading.width*bounded)+2)*(math.ceil(reading.height*bounded)+2)>limit:
            low,high=0.,bounded
            for _ in range(48):
                mid=(low+high)/2
                if (math.ceil(reading.width*mid)+2)*(math.ceil(reading.height*mid)+2)<=limit:low=mid
                else:high=mid
            bounded=low
        return self.page.get_pixmap(matrix=extract.pymupdf.Matrix(bounded,bounded).prerotate(self.angle),
            clip=self.source_rect(reading),alpha=False,
            colorspace=extract.pymupdf.csGRAY if gray else extract.pymupdf.csRGB)

    def jpeg(self,rect=None,scale=3,quality=90,max_bytes=2*1024*1024):
        for requested in extract._scale_ladder(scale):
            pix=self.pixmap(rect,requested)
            data=pix.tobytes('jpg',jpg_quality=quality)
            if len(data)<=max_bytes:return data
        raise extract.RasterTooLarge('source display exceeds encoded-byte bound')


def inspection_tiles(rect,width=240,height=320,overlap=24):
    rect=_rect(rect)
    if min(width,height)<=overlap or overlap<0:raise ValueError('invalid inspection geometry')
    cols=max(1,math.ceil((rect.width-overlap)/(width-overlap)))
    rows=max(1,math.ceil((rect.height-overlap)/(height-overlap)))
    if cols*rows>MAX_INSPECTION_TILES:
        raise ValueError('source inspection exceeds tile count bound')
    xs=[rect.x0+min(i*(width-overlap),max(0,rect.width-width)) for i in range(cols)]
    ys=[rect.y0+min(i*(height-overlap),max(0,rect.height-height)) for i in range(rows)]
    return [extract.pymupdf.Rect(x,y,min(x+width,rect.x1),min(y+height,rect.y1)) for y in ys for x in xs]


def _rule_count(columns,length):
    # A fixed +/-5-degree source skew envelope, half-degree samples, and one
    # raster-pixel stroke tolerance. Require repeated almost-spanning strokes;
    # ordinary letter baselines and a single decorative rule are insufficient.
    best=0
    for half_degree in range(-10,11):
        slope=math.tan(math.radians(half_degree/2));counts={}
        for x,ys in enumerate(columns):
            for y in {round(y-slope*x)+dy for y in ys for dy in (-1,0,1)}:
                counts[y]=counts.get(y,0)+1
        hits=sorted(y for y,n in counts.items() if n>=length*.8)
        count=sum(1 for i,y in enumerate(hits) if i==0 or y>hits[i-1]+1)
        best=max(best,count)
    return best


def ruled_grid(display,rect):
    """Evidence of ruled relationships, not a semantic table classification."""
    rect=_rect(rect)&display.rect
    if min(rect.width,rect.height)<24:return None
    pix=display.pixmap(rect,scale=1,gray=True,max_pixels=GRID_QUERY_PIXELS)
    w,h=pix.width,pix.height;data=pix.samples
    columns=[[] for _ in range(w)];rows=[[] for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if data[y*w+x]<200:columns[x].append(y);rows[y].append(x)
    vertical=_rule_count(rows,h)
    if vertical<3:return None
    horizontal=_rule_count(columns,w)
    if horizontal<3:return None
    return {'version':VERSION,'horizontal_rules':horizontal,'vertical_rules':vertical,
            'reading_bbox':list(rect),'displayed_pdf_bbox':list(display.source_rect(rect)),
            'query_pixels':[w,h],'orientation':display.angle,'source_rotation':display.page.rotation}


def _connected_source_extents(display, check_cancelled=None):
    """Measure connected original ink, with a one-pixel scan-gap tolerance.

    This is an extent check after orthogonal ruling was established. A component
    is not itself evidence that prose is a table. Full-page context is the safe
    remainder if the grid cannot be connected reliably.
    """
    pix=display.pixmap(scale=1,gray=True,max_pixels=GRID_QUERY_PIXELS)
    w,h=pix.width,pix.height;raw=pix.samples;mask=bytearray(w*h)
    for y in range(h):
        if check_cancelled and y%128==0:check_cancelled()
        for x in range(w):
            if raw[y*w+x]<200:
                for yy in range(max(0,y-1),min(h,y+2)):
                    for xx in range(max(0,x-1),min(w,x+2)):mask[yy*w+xx]=1
    extents=[]
    for start in range(w*h):
        if not mask[start]:continue
        mask[start]=0;stack=[start];left=right=start%w;top=bottom=start//w
        while stack:
            pos=stack.pop();y,x=divmod(pos,w)
            left=min(left,x);right=max(right,x);top=min(top,y);bottom=max(bottom,y)
            for yy in range(max(0,y-1),min(h,y+2)):
                for xx in range(max(0,x-1),min(w,x+2)):
                    n=yy*w+xx
                    if mask[n]:mask[n]=0;stack.append(n)
        if right-left>=12 and bottom-top>=12:
            sx,sy=display.rect.width/w,display.rect.height/h
            extents.append(extract.pymupdf.Rect(max(0,left-1)*sx,max(0,top-1)*sy,
                min(w,right+2)*sx,min(h,bottom+2)*sy))
        if check_cancelled:check_cancelled()
    return extents


def grid_regions(book,doc,pno,provenance,check_cancelled=None):
    if provenance.get('layer')!='ocr':return {}
    display=SourceDisplay(doc,pno,provenance);regions={};extents=None
    for index,element in enumerate(book.pages[pno]):
        if check_cancelled:check_cancelled()
        if element.kind!='p' or element.table_row:continue
        if not element.bbox or element.bbox[2]<=element.bbox[0] or element.bbox[3]<=element.bbox[1]:continue
        proof=ruled_grid(display,element.bbox)
        if proof:
            rect=extract.pymupdf.Rect(element.bbox)
            if extents is None:extents=_connected_source_extents(display,check_cancelled)
            matching=[bound for bound in extents if (bound & rect).get_area()>=rect.get_area()*.8
                      and bound.width>=rect.width*.8 and bound.height>=rect.height*.8]
            if matching:
                complete=min(matching,key=lambda bound:bound.get_area())
                rect=complete | rect
                proof['extent_method']='connected_original_ruling'
            else:
                complete=rect=display.rect
                proof['extent_method']='complete_original_context'
            proof['region_id']=hashlib.sha256(json.dumps([VERSION,pno,display.angle,
                display.page.rotation,[round(v,6) for v in complete]],separators=(',',':')).encode()).hexdigest()
            proof['reading_bbox']=list(rect)
            proof['displayed_pdf_bbox']=list(display.source_rect(rect))
            regions[index]=proof
    grouped={}
    for index,proof in regions.items():grouped.setdefault(proof['region_id'],[]).append(index)
    for members in grouped.values():
        rect=extract.pymupdf.Rect(regions[members[0]]['reading_bbox'])
        for index in members[1:]:rect |= extract.pymupdf.Rect(regions[index]['reading_bbox'])
        for index in members:
            regions[index]['element_indices']=members
            regions[index]['reading_bbox']=list(rect)
            regions[index]['displayed_pdf_bbox']=list(display.source_rect(rect))
    return regions
