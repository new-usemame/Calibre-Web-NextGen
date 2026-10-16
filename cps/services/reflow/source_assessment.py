# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded native text-layer survey for the basic Reflow settings page.

This is neither a prepared conversion nor an AI eligibility/cost estimate. Full
source recovery belongs to the explicit background preparation and conversion.
"""
from . import assess,extract,source

VERSION='bounded-native-assessment-1'
MAX_SAMPLE_PAGES=40


def _pages(count):
    take=min(count,MAX_SAMPLE_PAGES)
    if take<2:return list(range(take))
    return sorted({round(i*(count-1)/(take-1)) for i in range(take)})


def _native_page(doc,pno):
    page=doc[pno];rect=page.rect
    # Text-only extraction omits decoded image bytes, font/glyph analysis and
    # drawing paths. Image metadata supplies area, never raster recognition.
    text=page.get_text('text',flags=extract.pymupdf.TEXTFLAGS_TEXT & ~extract.pymupdf.TEXT_PRESERVE_IMAGES)
    raw=extract.RawPage(pno=pno,width=rect.width,height=rect.height)
    if text.strip():
        span=extract.Span(text=text,size=0,font='',flags=0,bbox=tuple(rect),origin_y=0)
        raw.blocks=[extract.Block(number=0,bbox=tuple(rect),lines=[extract.Line(spans=[span],bbox=tuple(rect))])]
    area=rect.get_area() or 1
    raw.images=[extract.Image(bbox=tuple(row['bbox']),area_ratio=(extract.pymupdf.Rect(row['bbox']) & rect).get_area()/area)
                for row in page.get_image_info(hashes=False,xrefs=False)]
    return raw


def survey(doc):
    count=doc.page_count;pages=_pages(count)
    raw=[_native_page(doc,pno) for pno in pages]
    assessment=assess.assess_pages(raw);wanted=source.candidates(raw)
    sampled=len(pages);estimated=sampled<count
    def projected(reason):
        observed=sum(1 for _,why in wanted if why==reason)
        return round(observed*count/sampled) if sampled else 0
    image_only=projected('image_only');damaged=min(count-image_only,projected('damaged_layer'))
    return dict(pages=count,sampled=sampled,sampled_page_indices=pages,
        assessment_scope='sample' if estimated else 'complete',
        verdict=assessment.verdict,text_layer=assessment.verdict not in ('NO_TEXT_LAYER','GARBAGE_TEXT'),
        non_latin_share=assessment.non_latin_share,
        reasons={'sampled_image_only':sum(why=='image_only' for _,why in wanted),
                 'sampled_damaged_layer':sum(why=='damaged_layer' for _,why in wanted)},
        ocr_candidates=image_only+damaged,ocr_image_only=image_only,ocr_damaged=damaged,
        counts_estimated=estimated,ocr_estimated_seconds=None)
