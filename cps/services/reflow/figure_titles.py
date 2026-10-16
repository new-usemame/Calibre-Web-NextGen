# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep a tightly attached native diagram title in its original printed crop."""
import math
from . import extract


def regions(raw, kept, images, candidates, style, probe):
    from .skeleton import Region
    doc=getattr(probe, '_doc', None)
    em=style.body_size
    if (doc is None or getattr(probe, '_pno', None)!=raw.pno or not em
            or raw.is_page_scan or raw.source_geometry.get('layer')=='ocr'
            or getattr(raw, 'transcript_unverified', False)):
        return kept, images, [], []
    try:
        page=doc[raw.pno]
        if abs(page.rect.width-raw.width)>.01 or abs(page.rect.height-raw.height)>.01:
            return kept, images, [], []
    except Exception:
        return kept, images, [], []
    lines=[ln for _,group in kept for ln in group]
    claimed=set();removed=set();figures=[];artwork=[]
    def overlaps(a,b):
        return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]
    for image in images:
        b=image.bbox;w=b[2]-b[0]
        possible=[]
        for ln in lines:
            a=ln.bbox;text=ln.stripped
            if (id(ln) in claimed or not 3<=len(text.split())<=12
                    or text.endswith(('.', '!', '?', ';', ':'))
                    or not em*1.05<max((sp.size for sp in ln.spans),default=0)<=em*1.8
                    or getattr(ln,'transcription_uncertain',False)
                    or any(sp.uncertain or sp.font=='ocr' or any(getattr(sp,flag,False)
                               for flag in ('encoding_unresolved','transcription_uncertain','punctuation_uncertain'))
                           for sp in ln.spans)
                    or not 0<=b[1]-a[3]<=em*.1
                    or w<=0 or abs((a[0]+a[2]-b[0]-b[2])/2)>w*.025
                    or a[2]-a[0]>w*1.5):
                continue
            # A chapter-opening title is not justified by a following image.
            if not any(other.bbox[3]<=a[1]-em and other.bbox[2]-other.bbox[0]>=w
                       and em*.9<=other.size<=em*1.05 for other in lines):
                continue
            box=(min(a[0],b[0])-1,min(a[1],b[1])-1,
                 max(a[2],b[2])+1,max(a[3],b[3])+1)
            if (not all(math.isfinite(v) for v in box) or box[0]<0 or box[1]<0
                    or box[2]>raw.width or box[3]>raw.height
                    or any(other is not image and overlaps(box,other.bbox) for other in images)
                    or any(overlaps(box,c.bbox) for c in candidates)
                    or any(other is not ln and overlaps(box,other.bbox)
                           and not (b[0]<=other.bbox[0] and b[1]<=other.bbox[1]
                                    and other.bbox[2]<=b[2] and other.bbox[3]<=b[3])
                           for other in lines)):
                continue
            try:
                if not extract.region_has_ink(doc,raw.pno,a,thresh=.005):continue
                if not extract.region_has_ink(doc,raw.pno,b,thresh=.005):continue
            except Exception:
                continue
            possible.append((ln,box))
        if len(possible)!=1:continue
        ln,box=possible[0];claimed.add(id(ln));removed.add(id(image))
        artwork.append(Region(kind='artwork',lines=[ln],bbox=box,
                              reason='source_figure_title'))
        # Render the union from the PDF: extracting the embedded image's bytes
        # would omit the external title and lose its printed face/alignment.
        figures.append(Region(kind='figure',bbox=box,reason='source_figure_title'))
    return ([(blk,[ln for ln in group if id(ln) not in claimed])
             for blk,group in kept if any(id(ln) not in claimed for ln in group)],
            [im for im in images if id(im) not in removed], figures, artwork)
