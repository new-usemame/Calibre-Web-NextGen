"""Scan borders must be distinguishable from source content."""
import io
import pytest
from PIL import Image, ImageDraw
from cps.services.reflow import crop_edges
pytestmark = pytest.mark.unit


def test_scan_bar_trim_keeps_list_pixels_and_refuses_rules_or_connected_artwork():
    image = Image.new('RGB', (824, 362), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(30, 340, 25):
        draw.rectangle((280, y, 440, y+9), fill='black')
    draw.rectangle((710, 8, 775, 354), fill='black')
    source = io.BytesIO(); image.save(source, format='PNG')
    assert len(crop_edges.scan_borders(image)) == 1
    data, trims = crop_edges.trim_scan_borders(source.getvalue())
    retained = Image.open(io.BytesIO(data))
    assert trims and not crop_edges.scan_borders(retained)
    assert retained.tobytes() == image.crop(trims[0]['retained_pixel_bbox']).tobytes()
    assert retained.crop((280, 30, 441, 340)).tobytes() == image.crop((280, 30, 441, 340)).tobytes()
    rule = Image.new('RGB', image.size, 'white')
    ImageDraw.Draw(rule).line((710, 0, 710, 361), fill='black', width=2)
    assert not crop_edges.scan_borders(rule)
    note = Image.new('RGB', (518, 148), 'white')
    draw = ImageDraw.Draw(note)
    draw.rectangle((0, 5, 517, 8), fill='black')
    draw.text((50, 60), '1 Further, brackets rotate.', fill='black')
    assert not crop_edges.scan_borders(note)
    filled = Image.new('RGB', image.size, 'black')
    assert not crop_edges.scan_borders(filled)


def test_primary_and_enlarged_reading_crops_trim_bars_but_original_page_stays_exact(tmp_path):
    import json, zipfile, pymupdf
    from cps.services.reflow import assemble, build_epub
    image = Image.new('1', (824, 362), 1)
    draw = ImageDraw.Draw(image)
    draw.rectangle((280, 30, 440, 330), outline=0, width=2)
    draw.rectangle((710, 8, 775, 354), fill=0)
    encoded = io.BytesIO(); image.save(encoded, format='PNG')
    source = tmp_path/'scan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=824, height=362)
        page.insert_image(page.rect, stream=encoded.getvalue())
        doc.save(source)
    element = assemble.Element(kind='fig', pno=0, bbox=(0,0,824,362))
    book = assemble.Book(pages={0:[element]}, elements=[element],
        figures=[dict(pno=0,bbox=(0,0,824,362),found='source',needs_ink=False)])
    target = tmp_path/'scan.epub'
    with pymupdf.open(source) as doc:
        build_epub.build(book, target, doc=doc)
    assert build_epub.validate(target) == []
    with zipfile.ZipFile(target) as z:
        payload = json.loads(z.read('META-INF/reflow.json'))
        evidence = payload['source_evidence'][0]
        original = Image.open(io.BytesIO(z.read('OEBPS/'+evidence['full'])))
        assert crop_edges.scan_borders(original)
        detail = next(d for d in evidence['details'] if d['id']=='figure_0')
        assert detail['scan_border_trim'] and detail['reading_bbox'][2]<detail['original_reading_bbox'][2]
        assert not crop_edges.scan_borders(Image.open(io.BytesIO(z.read('OEBPS/'+detail['src']))))
        primary = next(n for n in z.namelist() if n.startswith('OEBPS/images/fig_p0000_'))
        assert not crop_edges.scan_borders(Image.open(io.BytesIO(z.read(primary))))

