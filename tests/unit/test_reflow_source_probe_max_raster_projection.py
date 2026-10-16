"""The bounded real source raster must preserve far-edge query coordinates."""
import pymupdf
import pytest
from cps.services.reflow import extract
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit

def test_oversized_original_vector_source_mark_and_blank_neighbor_project_correctly():
 # This is a constructed vector PDF, not a fake pixmap. A near-far-corner mark
 # makes a small full-frame scale mismatch materially change source ownership.
 with pymupdf.open()as source:
  page=source.new_page(width=10000,height=10000)
  mark=pymupdf.Rect(9980,9980,9984,9984)
  blank=pymupdf.Rect(9972,9972,9976,9976)
  page.draw_rect(mark,color=(0,0,0),fill=(0,0,0),width=0)
  original=source.tobytes()
  # Independent direct source clips establish literal ink/absence, without
  # allocating a giant raster or mirroring the cache's projection arithmetic.
  with pymupdf.open(stream=original,filetype='pdf')as oracle:
   dark=oracle[0].get_pixmap(clip=mark,matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csGRAY,alpha=False)
   empty=oracle[0].get_pixmap(clip=blank,matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csGRAY,alpha=False)
   assert dark.width==dark.height==8 and any(v<100 for v in dark.samples)
   assert empty.width==empty.height==8 and all(v==255 for v in empty.samples)
  with SourceDisplay(source,0).query_document()as reading:
   probe=extract.ScanPixelProbe(reading,0)
   assert probe.source_ink_bounds(mark)is not None
   # Check the real cached full gray allocation; no RGB/giant model image.
   data,width,height,_=probe._source_raster
   assert width*height<=extract.MAX_RASTER_PIXELS==4096*4096
   assert len(data)==width*height<=16_777_216
   assert probe.source_ink_bounds(blank)is None
   assert not probe.source_has_ink(blank)
   assert probe.source_ink_bounds((10001,10001,10005,10005))is None
