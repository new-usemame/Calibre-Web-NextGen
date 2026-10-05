"""Caption-bounded source keys on an independently measured scanned spread.

No glyph is interpreted. Complete raster rows, captions, and the opposite leaf
stay in source images. Unknown ink outside the proposed panels refuses the split.
"""
import math
from statistics import median
from . import extract

SCALE=2
MAX_PIXELS=1_200_000


def _pixels(doc,pno,rect,*,source_colors=False):
    if (len(rect)!=4 or not all(math.isfinite(v) for v in rect)
            or rect[2]<=rect[0] or rect[3]<=rect[1]
            or (math.ceil((rect[2]-rect[0])*SCALE)+2)*
               (math.ceil((rect[3]-rect[1])*SCALE)+2)>MAX_PIXELS):return None
    clip=extract.pymupdf.Rect(rect)&doc[pno].rect
    if clip.is_empty or any(abs(a-b)>.01 for a,b in zip(clip,rect)):return None
    pix=doc[pno].get_pixmap(matrix=extract.pymupdf.Matrix(SCALE,SCALE),clip=clip,
        colorspace=extract.pymupdf.csRGB if source_colors else extract.pymupdf.csGRAY,alpha=False)
    return pix


def _bounds(pix,threshold=240):
    from PIL import Image,ImageChops
    if pix.n==3 and threshold==255:
        image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        return ImageChops.difference(image,Image.new('RGB',image.size,'white')).getbbox()
    image=Image.frombytes('L',(pix.width,pix.height),pix.samples)
    return image.point(lambda value:255 if value<threshold else 0).getbbox()



def panels(raw,probe):
    """Return complete non-overlapping source owners, or abstain as a whole."""
    if (not raw.is_page_scan or probe is None or raw.width<1.25*raw.height
            or getattr(raw,'transcript_unverified',False)):return []
    doc=getattr(probe,'_doc',None)
    if doc is None:return []
    split=raw.width/2
    lines=[ln for b in raw.text_blocks for ln in b.lines if ln.stripped]
    left=[ln for ln in lines if ln.bbox[2]<split-raw.width*.04]
    right=[ln for ln in lines if ln.bbox[0]>split+raw.width*.04]
    if len(left)+len(right)!=len(lines) or len(left)<10:return []
    # The opposite leaf is independent sustained material, not a parallel label
    # column of the same key. Preserve its complete half-page, including assets.
    if sum(ln.bbox[2]-ln.bbox[0]>=raw.width*.25 for ln in left)<8:return []
    from .skeleton import CAPTION_LINE
    captions=sorted([ln for ln in right if CAPTION_LINE.match(ln.stripped)
        and 4<=len(ln.stripped.split())<=12],key=lambda ln:ln.bbox[1])
    if not 2<=len(captions)<=4:return []
    source=[];owned=set();previous=raw.height*.12
    for caption in captions:
        if caption.bbox[1]<=previous:return []
        group=[ln for ln in right if ln is not caption and ln.bbox[1]>=previous
               and ln.bbox[3]<=caption.bbox[1]]
        labels=[ln for ln in group if len(ln.stripped.split())<=5
                and ln.bbox[2]-ln.bbox[0]<=raw.width*.18
                and ln.bbox[3]-ln.bbox[1]<=2*max(1,caption.size)]
        if len(labels)<7:return []
        em=max(1,median(ln.size for ln in labels));start=median(ln.bbox[0] for ln in labels)
        if (any(abs(ln.bbox[0]-start)>raw.width*.012 for ln in labels)
                or max(ln.bbox[3] for ln in labels)-min(ln.bbox[1] for ln in labels)<raw.height*.16
                or abs((caption.bbox[0]+caption.bbox[2])/2-
                       (min(ln.bbox[0] for ln in group)+max(ln.bbox[2] for ln in group))/2)>raw.width*.1):return []
        # A column of unreadable marks may be one tall OCR line; every other
        # recognized line must be one of the aligned short labels.
        strips=[ln for ln in group if ln not in labels
                and ln.bbox[2]<start and ln.bbox[2]-ln.bbox[0]<=raw.width*.035
                and ln.bbox[3]-ln.bbox[1]>=raw.height*.05
                and all(sp.font.lower()=='ocr' and sp.uncertain for sp in ln.spans)]
        if len(labels)+len(strips)!=len(group):return []
        top=min(ln.bbox[1] for ln in group)-2*em
        bottom=caption.bbox[3]+2*em
        if top<previous or bottom>raw.height:return []
        query=(split,top,raw.width,bottom)
        try:
            pix=_pixels(doc,raw.pno,query,source_colors=True);extent=_bounds(pix,255) if pix else None
        except (ValueError,RuntimeError,AttributeError):return []
        if not extent or min(extent[0],extent[1],pix.width-extent[2],pix.height-extent[3])<2:return []
        # Repeated actual marks immediately left of independently aligned label
        # rows prove the relationship. Missing marks and broad chart strokes
        # refuse; neither vocabulary nor OCR confidence supplies glyph meaning.
        strip=(max(split,start-raw.width*.09),top,start-raw.width*.006,caption.bbox[1])
        try:marks=_pixels(doc,raw.pno,strip)
        except (ValueError,RuntimeError,AttributeError):return []
        if marks is None:return []
        data=marks.samples;runs=[]
        for y in range(marks.height):
            dark=[x for x,v in enumerate(data[y*marks.width:(y+1)*marks.width]) if v<240]
            if not dark:continue
            if runs and y-runs[-1][-1][0]<=max(1,int(.15*em*SCALE)):
                runs[-1].append((y,min(dark),max(dark)+1))
            else:runs.append([(y,min(dark),max(dark)+1)])
        centers=[];matched=set()
        for run in runs:
            x0=min(row[1] for row in run);x1=max(row[2] for row in run)
            y0=run[0][0];y1=run[-1][0]+1
            width=(x1-x0)/SCALE;height=(y1-y0)/SCALE
            if height<.25*em:continue  # a source divider is preserved, not a row mark
            if not (.15*em<=width<=3*em and height<=2*em):return []
            if min(x0,y0,marks.width-x1,marks.height-y1)<1:return []
            center_y=(marks.y+(y0+y1)/2)/SCALE
            nearest=min(labels,key=lambda ln:abs((ln.bbox[1]+ln.bbox[3])/2-center_y))
            if abs((nearest.bbox[1]+nearest.bbox[3])/2-center_y)>em:return []
            if id(nearest) in matched:return []
            matched.add(id(nearest));centers.append((marks.x+(x0+x1)/2)/SCALE)
        if len(centers)<7 or len(centers)<len(labels)*.65 or max(centers)-min(centers)>em*.6:return []
        box=(max(split,min((pix.x+extent[0]-2)/SCALE,min(ln.bbox[0] for ln in group+[caption]))),
             max(top,min((pix.y+extent[1]-2)/SCALE,min(ln.bbox[1] for ln in group+[caption]))),
             min(raw.width,max((pix.x+extent[2]+2)/SCALE,max(ln.bbox[2] for ln in group+[caption]))),
             min(bottom,max((pix.y+extent[3]+2)/SCALE,max(ln.bbox[3] for ln in group+[caption]))))
        # A distant annotation can enlarge the all-ink extent without belonging
        # to this captioned object. Keep ambiguous wide bands under the existing
        # source policy instead of merging unrelated marginal material.
        if (extent[2]-extent[0])/SCALE>raw.width*.35:return []
        if any(not (box[0]<=ln.bbox[0] and box[1]<=ln.bbox[1]
                    and ln.bbox[2]<=box[2] and ln.bbox[3]<=box[3]) for ln in group+[caption]):return []
        source.append((query,box,group+[caption]));owned.update(id(ln) for ln in group+[caption])
        previous=caption.bbox[3]+em
    # All remaining right-leaf text belongs to its top margin. Source ink in
    # gaps or below the final key refuses the split rather than silently dropping
    # an unrecognized asset or unrelated paragraph.
    headers=[ln for ln in right if id(ln) not in owned]
    if any(ln.bbox[3]>source[0][0][1] for ln in headers):return []
    gaps=[(split,a[0][3],raw.width,b[0][1]) for a,b in zip(source,source[1:])]
    gaps.append((split,source[-1][0][3],raw.width,raw.height))
    for gap in gaps:
        if gap[3]<=gap[1]:return []
        try:
            pix=_pixels(doc,raw.pno,gap,source_colors=True)
            if pix is None or _bounds(pix,255):return []
        except (ValueError,RuntimeError,AttributeError):return []
    result=[((0,0,split,raw.height),left,'sparse_scan_spread_panel',0)]
    top_box=(split,0,raw.width,source[0][0][1])
    try:top_ink=_bounds(_pixels(doc,raw.pno,top_box,source_colors=True),255)
    except (ValueError,RuntimeError,AttributeError):return []
    if headers or top_ink:result.append((top_box,headers,'sparse_scan_spread_panel',1))
    result.extend((box,group,'uncertain_scan_key_panel',1) for _,box,group in source)
    return result
