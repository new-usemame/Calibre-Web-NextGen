"""SOURCE-free actual EPUB controls; original pixels and live reading survive.

Synthetic locked optical words are independent of model hints. Ambiguous art
may use explicit optional full-page context; it may never crop off corners,
rasterize BODY, silently disappear or abort other objects and later pages.
"""
import copy
import importlib.util
import io
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import assemble, build_epub, vision_pipeline

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_spec = importlib.util.spec_from_file_location('round5_locked_ink', _helper)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
pytestmark = pytest.mark.unit


def art(page, rect, ident, hint=None, role='figure'):
    page.page.draw_rect(rect, color=(0, 0, 0), fill=(.85, .85, .85), width=2)
    page.page.draw_line((rect[0], rect[1]), (rect[2], rect[3]), color=(0, 0, 0), width=2)
    for x in (rect[0], rect[2] - 6):
        for y in (rect[1], rect[3] - 6):
            page.page.draw_rect([x, y, x + 6, y + 6], color=(0, 0, 1), fill=(0, 0, 1))
    page.blocks.append(h.model_block('A complete synthetic source object', ident, role, hint or rect))


def paragraph(page, lines, positions, ident='body', previous=False, next=False):
    boxes = []
    for line, (x, y) in zip(lines, positions):
        page.page.insert_text((x, y), line, fontsize=7, color=(1, 0, 0))
        boxes.append([x - 1, y - 9, x + pymupdf.get_text_length(line, fontsize=7) + 1, y + 3])
    box = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    page.blocks.append(h.model_block(' '.join(lines), ident, 'body', box, previous=previous, next=next))


def image_bytes(path, src):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith('/' + src) or n == src)
        return z.read(name)


def pixels(path, src):
    return Image.open(io.BytesIO(image_bytes(path, src))).convert('RGB')


def blue_corners(image, full_page=False, source_rect=None):
    # Whole original context need not put art at the image corners, but must
    # retain all four separated blue source corner clusters.
    if full_page:
        assert source_rect is not None, 'measure corners of this source object, not all art on the page'
        sx, sy = image.width / h.W, image.height / h.H
        x0, y0, x1, y1 = source_rect
        crop = image.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
        blue_corners(crop)
        return
    points = [(x, y) for y in range(image.height) for x in range(image.width)
              if (lambda c: c[2] > 140 and c[0] < 100 and c[1] < 100)(image.getpixel((x, y)))]
    assert points, 'complete original illustration remains accessible'
    xs = [x for x, y in points]; ys = [y for x, y in points]
    assert max(xs) - min(xs) > 30 and max(ys) - min(ys) > 30
    midx = (min(xs) + max(xs)) / 2; midy = (min(ys) + max(ys)) / 2
    assert {(x > midx, y > midy) for x, y in points} == {(False, False), (False, True), (True, False), (True, True)}
    if not full_page:
        assert min(xs) < image.width * .15 and max(xs) > image.width * .85
        assert min(ys) < image.height * .12 and max(ys) > image.height * .88


def context_links(path, result, docs):
    targets = {(name, node.get('id')): node for name, root in docs.items()
               for node in root.iter() if node.get('id')}
    refs = [(name, a) for name, root in docs.items()
            for a in root.findall('.//x:a', h.NS)
            if 'source-disclosure-ref' in (a.get('class') or '').split()
            and __import__('hashlib').sha256((a.text or '').strip().encode()).hexdigest() == '8eea9d54b9d9adb5031e1ce6424bf1fa49ced0f94699ca9a2273703153e67531']
    for name, a in refs:
        assert a.get('{http://www.idpf.org/2007/ops}type') == 'noteref'
        owner = next(n for root in docs.values() for n in h.body_nodes({'x': root}) if a in list(n.iter()))
        assert owner.tag.endswith('}figure'), 'source-context control floats outside live sentence'
        target = h.destination(name, a.get('href'))
        assert target in targets, 'every degraded figure has a real reachable context'
        aside = targets[target]
        assert aside.tag.endswith('}aside') and 'vision-disclosure' in (aside.get('class') or '').split()
        images = aside.findall('.//x:img', h.NS)
        assert len(images) == 1
        src = images[0].get('src'); rec = result['images'][src]
        assert rec['kind'] == 'figure_source_reference'
        assert rec['bbox'] == pytest.approx([0, 0, h.W, h.H])
        image = pixels(path, src)
        assert image.width / image.height == pytest.approx(h.W / h.H, abs=.01)
        assert any(h.destination(target[0], back.get('href')) == (name, a.get('id'))
                   for back in aside.findall('.//x:a', h.NS)
                   if back.get('href') and '#' in back.get('href')), 'Return reaches this exact figure control'
        body_images = [img.get('src') for n in h.body_nodes(docs) for img in n.findall('.//x:img', h.NS)]
        assert src not in body_images, 'whole page is optional source evidence, never BODY raster'
    return refs


def whole_art_or_context(path, result, docs, ident, rect):
    normal = [(src, rec) for src, rec in result['images'].items()
              if rec['kind'] in ('figure', 'table') and src.endswith('_' + ident + '.jpg')]
    if normal:
        assert len(normal) == 1
        src, rec = normal[0]
        assert rec['bbox'][0] <= rect[0] and rec['bbox'][1] <= rect[1]
        assert rec['bbox'][2] >= rect[2] and rec['bbox'][3] >= rect[3], 'never clip complete SOURCE art to a narrow gap'
        im = pixels(path, src); blue_corners(im)
        assert not any(r > 130 and g < 90 and b < 90 for r, g, b in im.getdata()), 'live prose pixels never become nominal art'
    else:
        refs = context_links(path, result, docs)
        assert refs, 'an uncertain complete object remains accessible, never silently omitted'
        cases = [c for c in result['degraded_figures'] if c['block'] == ident]
        assert len(cases) == 1 and cases[0]['reason']
        contexts = [(src, rec) for src, rec in result['images'].items() if rec['kind'] == 'figure_source_reference']
        assert contexts
        assert any((lambda im: bool([(x, y) for y in range(im.height) for x in range(im.width)
                     if (lambda c: c[2] > 140 and c[0] < 100 and c[1] < 100)(im.getpixel((x, y)))]))(pixels(path, src))
                   for src, rec in contexts)
        for src, rec in contexts: blue_corners(pixels(path, src), full_page=True, source_rect=rect)


@pytest.mark.parametrize('side', ['left', 'right'])
@pytest.mark.parametrize('vertical', ['upper', 'lower'])
def test_every_correlated_paragraph_row_inside_coarse_art_hint_stays_live(tmp_path, monkeypatch, side, vertical):
    p = h.InkPage(); shift = 0 if vertical == 'upper' else 100
    rect = [25, 85 + shift, 80, 180 + shift] if side == 'left' else [220, 85 + shift, 275, 180 + shift]
    hint = [40, 96 + shift, 100, 164 + shift] if side == 'left' else [200, 96 + shift, 260, 164 + shift]
    art(p, rect, 'wrapped', hint)
    lines = ['Ruby prose stays beside the object.',
             'Every following source row remains live.',
             'The last printed row also stays readable.']
    ends = [184, 212, 212]
    positions = [(112, 111 + shift), (88, 131 + shift), (88, 151 + shift)] if side == 'left' else [
        (end - pymupdf.get_text_length(line, fontsize=7), y + shift)
        for line, end, y in zip(lines, ends, [111, 131, 151])]
    paragraph(p, lines, positions)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert h.body_texts(docs) == [' '.join(lines)]
    whole_art_or_context(path, result, docs, 'wrapped', rect)


@pytest.mark.parametrize('broad_row', ['above', 'below'])
def test_fullwidth_to_wrapped_rows_never_apply_global_paragraph_edge(tmp_path, monkeypatch, broad_row):
    p = h.InkPage(); rect = [25, 105, 82, 195]
    art(p, rect, 'transition', [39, 119, 99, 179])
    broad = 'A broad source line crosses the full column.'
    narrow = ['Narrow ruby words run beside the source art.', 'Later words continue in that same paragraph.']
    lines = [broad] + narrow if broad_row == 'above' else narrow + [broad]
    positions = [(24, 80), (112, 135), (112, 155)] if broad_row == 'above' else [(112, 135), (112, 155), (24, 226)]
    paragraph(p, lines, positions)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert h.body_texts(docs) == [' '.join(lines)]
    whole_art_or_context(path, result, docs, 'transition', rect)


def test_ambiguous_object_keeps_fullcontext_while_valid_object_notes_and_later_pages_publish(tmp_path, monkeypatch):
    first, last = h.InkPage(), h.InkPage()
    bad = [30, 85, 120, 165]
    art(first, bad, 'ambiguous', [45, 98, 130, 155])
    text = ['A live ruby sentence crosses the coarse source object.', 'Every real source word remains live and unique.']
    paragraph(first, text, [(110, 119), (110, 139)], ident='overlap')
    good = [160, 224, 260, 284]
    art(first, good, 'valid')
    first.text('A separate valid diagram.', 'cap', 'caption', y=301, size=7)
    later = 'A later page cites a source note and survives the earlier ambiguous object.'
    last.text(later, 'later', model='A later page cites<n:n7> a source note and survives the earlier ambiguous object.')
    note = 'A real printed note remains outside live body and returns correctly.'
    last.text('7 ' + note, 'n7', 'footnote', y=325, size=7, marker='7', model=note)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [first, last])
    assert h.body_texts(docs) == [' '.join(text), later]
    h.assert_exact_returns(docs, 1)
    refs = context_links(path, result, docs)
    assert len(refs) == 1
    assert result['degraded_figure_count'] == 1
    assert len(result['degraded_figures']) == 1
    case = result['degraded_figures'][0]
    assert case['page'] == 0 and case['block'] == 'ambiguous' and case['reason']
    assert case['seed_bbox'] == pytest.approx([45, 98, 130, 155])
    pagecase = result['pages'][0]
    assert pagecase['degraded_figure_count'] == 1 and pagecase['degraded_figures'] == [case]
    assert result['pages'][1]['degraded_figure_count'] == 0
    assert set(result['page_homes']) == {0, 1}
    assert any(rec['kind'] == 'figure' and src.endswith('_valid.jpg') for src, rec in result['images'].items())
    source = next(src for src, rec in result['images'].items() if rec['kind'] == 'figure_source_reference')
    blue_corners(pixels(path, source), full_page=True, source_rect=bad)

