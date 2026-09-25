"""A scanned symbol key keeps printed row associations when OCR cannot read them."""

import io
import zipfile

import pymupdf
import pytest
from PIL import Image

from cps.services.reflow import assemble, build_epub, extract, skeleton


def _line(text, box, uncertain=False):
    span = extract.Span(text, 10, 'ocr', 0, box, box[3], uncertain=uncertain)
    return extract.Line([span], box)


def _key_page(*, reliable_strip=False, prose=False, second_panel=False):
    lines = [_line('Printed reference', (210, 45, 300, 60))]
    # Recognition has fused several vertically printed marks into tall lines.
    for i in range(3):
        box = (72, 90+i*110, 82, 187+i*110)
        lines.append(_line('unreadable marks', box, uncertain=not reliable_strip))
    for i in range(12):
        y = 94+i*27
        lines.append(_line('Entry '+str(i), (104, y, 158, y+10)))
        lines.append(_line('associated value', (215, y, 300, y+10)))
    if prose:
        lines.append(_line('This ordinary sentence describes the surrounding material in complete prose.',
                           (105, 468, 355, 480)))
    if second_panel:
        lines.append(_line('Independent material', (12, 150, 60, 162)))
    blocks = [extract.Block(i, line.bbox, [line]) for i, line in enumerate(lines)]
    return extract.RawPage(0, 400, 500, blocks,
                           [extract.Image((0, 0, 400, 500), 1)])


def test_uncertain_symbol_key_is_one_primary_source_object_with_complete_rows():
    raw = _key_page()
    skel = skeleton.page_skeleton(raw, skeleton.book_style([raw]))
    book = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
    assert book.conservation.ok
    assert [r.kind for r in skel.regions] == ['artwork', 'figure']
    assert [f['found'] for f in book.figures] == ['uncertain_scan_key_panel']
    assert tuple(book.figures[0]['bbox']) == (0, 0, raw.width, raw.height)
    assert len(book.artwork) == 1
    assert all(line.stripped in book.artwork[0]['text']
               for block in raw.text_blocks for line in block.lines)
    assert book.needs_source_evidence(0)


@pytest.mark.parametrize('change', ['reliable_strip', 'prose', 'second_panel'])
def test_symbol_key_fallback_requires_uncertain_marks_and_one_sparse_panel(change):
    raw = _key_page(**{change: True})
    skel = skeleton.page_skeleton(raw, skeleton.book_style([raw]))
    assert 'uncertain_scan_key_panel' not in skel.reasons


@pytest.mark.parametrize('mark,color', [
    ((48, 94, 80, 104), (0, 0, 0)),       # beyond the OCR symbol's left edge
    ((20, 200, 105, 218), (0, 0, 0)),     # a wide unreadable printed mark
    ((0, 94, 20, 104), (.75, .7, .65)),   # pale source ink at the page boundary
    ((365, 94, 395, 104), (.1, .5, .8)), # colored ink beyond the label bounds
    ((48, 0, 80, 12), (0, 0, 0)),        # source-only mark above the title
    ((48, 485, 80, 500), (0, 0, 0)),    # source-only mark below the last row
])
@pytest.mark.parametrize('probe_kind', ['real', 'unavailable'])
def test_key_primary_package_retains_complete_source_pixels(mark, color, probe_kind, tmp_path):
    raw = _key_page()
    with pymupdf.open() as painting:
        page = painting.new_page(width=raw.width, height=raw.height)
        page.draw_rect(mark, color=None, fill=color)
        for i in range(12):
            y = 94+i*27
            page.insert_text((104, y+9), 'Entry '+str(i), fontsize=9)
            page.insert_text((215, y+9), 'associated value', fontsize=9)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png')
    with pymupdf.open() as doc:
        page = doc.new_page(width=raw.width, height=raw.height)
        page.insert_image(page.rect, stream=pixels)
        if probe_kind == 'real':
            probe = extract.ScanPixelProbe(doc, 0)
        else:
            class UnusableProbe:
                def __getattr__(self, name):
                    raise RuntimeError('pixel proof is unavailable')
            probe = UnusableProbe()
        style = skeleton.book_style([raw])
        book = assemble.assemble([skeleton.page_skeleton(raw, style, pixel_probe=probe)],
                                 style, [raw])
        assert book.conservation.ok
        assert tuple(book.figures[0]['bbox']) == (0, 0, raw.width, raw.height)
        target = tmp_path/'key.epub'
        build_epub.build(book, target, doc=doc)
        assert build_epub.validate(target) == []
        with zipfile.ZipFile(target) as package:
            primary = package.read('OEBPS/images/fig_p0000_0.jpg')
            assert primary == extract.crop_jpeg(doc, 0, (0, 0, raw.width, raw.height))
            with Image.open(io.BytesIO(primary)) as image:
                assert image.size == (raw.width*2, raw.height*2)
                x = round((mark[0]+mark[2]))
                y = round((mark[1]+mark[3]))
                assert min(image.convert('RGB').getpixel((x, y))) < 230
            chapter = package.read('OEBPS/ch001.xhtml').decode()
            assert chapter.count('Complete original page containing a printed symbol key.') == 1
            assert 'original-p0000.xhtml#page' in chapter
