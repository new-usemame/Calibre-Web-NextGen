"""A reader can downscale a figure without losing ink at the bitmap boundary."""
import io
from types import SimpleNamespace
from PIL import Image, ImageChops
import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, extract

pytestmark = pytest.mark.unit


def test_published_figure_has_white_margin_and_every_original_pixel():
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=200, height=200)
        box = (40, 50, 140, 150)
        # A printed table's outer rules touch all four crop boundaries.
        page.draw_rect(box, color=(0, 0, 0), width=2)
        page.draw_line((40, 100), (140, 100), width=1)
        book = assemble.Book(figures=[dict(pno=0, bbox=box)])
        chapters = [SimpleNamespace(blocks=['<figure><img src="images/fig_p0000_0.jpg"/></figure>'])]
        data = {}
        class Sink:
            def image(self, src, pixels): data[src] = pixels
        assert build_epub._figure_images(chapters, doc, book, Sink()) == ([], [])
        original = Image.open(io.BytesIO(extract.crop_jpeg(doc, 0, box))).convert('RGB')
        emitted = Image.open(io.BytesIO(data['images/fig_p0000_0.jpg'])).convert('RGB')
        margin = (emitted.width-original.width)//2
        assert margin >= 8
        assert emitted.size == (original.width+2*margin, original.height+2*margin)
        assert ImageChops.difference(original, emitted.crop((margin, margin, emitted.width-margin, emitted.height-margin))).getbbox() is None
        for edge in ((0,0,emitted.width,margin),(0,emitted.height-margin,emitted.width,emitted.height),
                     (0,0,margin,emitted.height),(emitted.width-margin,0,emitted.width,emitted.height)):
            assert emitted.crop(edge).getextrema() == ((255,255),)*3
    finally:
        doc.close()
