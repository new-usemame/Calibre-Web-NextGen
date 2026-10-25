"""Synthetic raster sampling preserves bounded source recognition detail."""
import io
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import local_reading, ocr
from cps.services.reflow.source_display import SourceDisplay

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('density,expected_scale', [(1, 2), (4, 3)])
def test_low_density_page_avoids_excessive_raster_enlargement(tmp_path, monkeypatch, density, expected_scale):
    image = Image.new('RGB', (240*density, 300*density), 'white')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    monkeypatch.setattr(ocr, '_engine', lambda language: ('synthetic-engine', 'version', 'data'))
    seen = []
    def invoke(args, directory, **kwargs):
        picture = Image.open(directory/'page.png')
        seen.append(picture.size)
        (directory/'result.tsv').write_text(
            'level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n'
            '5\t1\t1\t1\t20\t40\t80\t20\t96\tcomplete\n')
        return 0, '', ''
    monkeypatch.setattr(ocr, '_invoke', invoke)
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=300)
        page.insert_image(page.rect, stream=stream.getvalue())
        words, receipt = local_reading.recognize(SourceDisplay(document, 0), tmp_path)
    assert seen == [(240*expected_scale, 300*expected_scale)]
    assert [word['text'] for word in words] == ['complete']
