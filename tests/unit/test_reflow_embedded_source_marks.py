"""Visible narrow source images survive title pages without promoting furniture."""
import io
import zipfile
from tests.unit.reflow_image_assertions import figure_name

import pymupdf
from PIL import Image, ImageDraw

from cps.services.reflow import build_epub, pipeline


def _mark():
    image = Image.new('RGB', (250, 15), 'white')
    ImageDraw.Draw(image).text((3, 1), 'PRINTED PUBLISHER MARK', fill='black')
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return output.getvalue()


def test_sparse_title_page_retains_embedded_text_pixels_with_source_access(tmp_path):
    doc = pymupdf.open()
    mark = _mark()
    title = doc.new_page(width=595, height=842)
    for y, text in ((100, 'BOOK TITLE'), (155, 'SUBTITLE'), (320, 'AUTHOR')):
        title.insert_text((230, y), text, fontsize=18)
    title.insert_image(pymupdf.Rect(173, 504, 423, 519), stream=mark)
    title.insert_text((294, 806), '2', fontsize=12)

    dense = doc.new_page(width=595, height=842)
    for index in range(14):
        dense.insert_text((80, 100 + index * 25), 'Ordinary body paragraph line %d' % index,
                          fontsize=11)
    dense.insert_image(pymupdf.Rect(173, 504, 423, 519), stream=mark)

    footer = doc.new_page(width=595, height=842)
    footer.insert_text((230, 130), 'ANOTHER TITLE', fontsize=18)
    footer.insert_text((230, 185), 'AUTHOR', fontsize=18)
    footer.insert_image(pymupdf.Rect(173, 770, 423, 785), stream=mark)

    header = doc.new_page(width=595, height=842)
    header.insert_text((230, 130), 'THIRD TITLE', fontsize=18)
    header.insert_text((230, 185), 'AUTHOR', fontsize=18)
    header.insert_image(pymupdf.Rect(173, 30, 423, 45), stream=mark)

    result = pipeline.run(doc, client=None, recovery_opts={'mode': 'off'})
    marks = [figure for figure in result.book.figures
             if figure.get('found') == 'embedded_source_mark']
    assert [figure['pno'] for figure in marks] == [0]
    target = tmp_path / 'title.epub'
    build_epub.build(result.book, str(target), page_html=result.page_html, doc=doc)
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as archive:
        bodies = [archive.read(name).decode() for name in archive.namelist()
                  if name.startswith('OEBPS/ch') and name.endswith('.xhtml')]
        name = figure_name(archive, 'fig_p0000_0')
        assert sum(name.removeprefix('OEBPS/') in body for body in bodies) == 1
        assert not any('fig_p0001_' in body or 'fig_p0002_' in body or
                       'fig_p0003_' in body for body in bodies)
        assert any('without inferred or searchable text' in body and
                   'original-p0000.xhtml#page' in body for body in bodies)
        assert 'OEBPS/original-p0000.xhtml' in archive.namelist()
        assert archive.read(name)
    doc.close()
