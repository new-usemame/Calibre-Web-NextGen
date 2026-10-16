"""Whole raster tables and footer signatures: source-free actual EPUB controls."""
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

_spec = importlib.util.spec_from_file_location('round4_table_footer_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


@pytest.mark.parametrize('hint', ['tiny', 'overbroad'])
def test_table_crop_contains_whole_connected_table_and_no_neighboring_live_prose(tmp_path, monkeypatch, hint):
    page = h.InkPage()
    text = 'A readable crimson explanation stays outside the table.'
    page.text([text, 'Another ordinary source line owns the separate upper panel.'],
              'body', color=(1, 0, 0))
    rect = page.figure(caption=False, hint=[110, 115, 165, 140] if hint == 'tiny' else [10, 25, 280, 205])
    page.blocks[-1]['role'] = 'table'
    page.blocks[-1]['text'] = 'A compact table with named columns and two synthetic rows.'
    page.page.insert_text((50, 114), 'Left   Middle   Far right header', fontsize=8)
    page.page.insert_text((50, 133), 'One       4           15', fontsize=8)
    page.page.insert_text((50, 153), 'Two       8           30', fontsize=8)
    page.text('A separate ordinary explanation follows the table.', 'after', y=222)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    tables = [(src, rec) for src, rec in result['images'].items() if rec['kind'] == 'table']
    assert len(tables) == 1
    src, rec = tables[0]
    box = rec['bbox']
    assert box[0] <= rect[0] and box[1] <= rect[1] and box[2] >= rect[2] and box[3] >= rect[3], 'column headers and corner ink of one table must not be clipped'
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(src))
        image = Image.open(io.BytesIO(archive.read(name))).convert('RGB')
    assert not any(r > 130 and g < 90 and b < 90 for r, g, b in image.getdata()), 'a table is not permission to rasterize adjacent body prose'
    assert text in ' '.join(h.body_texts(docs))
    assert sum(n.tag.endswith('}figure') for n in h.body_nodes(docs)) == 1


class RoundSignaturePage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        found = [w for w in words if w['text'] == 'O' and w['bbox'][1] > 370]
        assert len(found) == 1
        found[0]['text'] = '°'
        return image, words, reading


@pytest.mark.parametrize('glyph', ['Q', '0', 'O'])
def test_isolated_bottom_signature_or_folio_misclassified_figure_is_not_body_raster(tmp_path, monkeypatch, glyph):
    page = RoundSignaturePage() if glyph == 'O' else h.InkPage()
    text = 'The ordinary explanation occupies the readable page body.'
    page.text([text, 'Its final source line preserves a meaningful unfinished clause'], 'body', y=325)
    page.page.insert_text((178, 387), glyph, fontsize=7)
    page.blocks.append(h.model_block('An isolated printed footer mark', 'mark', 'figure', [173, 375, 196, 394]))
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert not [rec for rec in result['images'].values() if rec['kind'] == 'figure'], 'isolated source-sized bottom printer signature or folio must not appear as a tiny body image'
    assert text in ' '.join(h.body_texts(docs))
    assert not [n for n in h.body_nodes(docs) if n.tag.endswith('}figure')]


def test_small_bottom_connected_illustration_with_label_is_retained(tmp_path, monkeypatch):
    page = h.InkPage()
    page.text(['A short ordinary explanation precedes this compact device.',
               'The letter Q in prose is an ordinary meaningful symbol.'], 'body', y=280)
    rect = [145, 325, 215, 368]
    page.page.draw_rect(rect, color=(0, 0, 1), fill=(.9, .9, .9), width=2)
    page.page.draw_line((145, 325), (215, 368), color=(0, 0, 1), width=2)
    page.page.insert_text((173, 352), 'Q', fontsize=7)
    page.blocks.append(h.model_block('A labelled connected small device', 'art', 'figure', rect))
    page.text('A compact connected device.', 'caption', 'caption', y=385, size=6)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [rec for rec in result['images'].values() if rec['kind'] == 'figure']
    assert len(figures) == 1, 'a small genuine illustration and its internal letter remain present'
    assert figures[0]['bbox'][0] <= rect[0] and figures[0]['bbox'][2] >= rect[2]
    assert 'The letter Q in prose is an ordinary meaningful symbol.' in ' '.join(h.body_texts(docs))


def test_two_distinct_tables_without_intervening_prose_are_not_duplicate_whole_gap_crops(tmp_path, monkeypatch):
    page = h.InkPage()
    page.text('A complete ordinary introduction precedes both tables.', 'body', y=50)
    rectangles = [[35, 100, 260, 160], [35, 210, 260, 270]]
    for i, rect in enumerate(rectangles):
        color = (0, 0, 1) if i == 0 else (0, 1, 0)
        page.page.draw_rect(rect, color=color, fill=(.9, .9, .9), width=2)
        page.page.draw_line((rect[0], rect[1]), (rect[2], rect[3]), color=color, width=2)
        page.page.insert_text((50, rect[1] + 25), 'Name     First     Second', fontsize=8)
        page.page.insert_text((50, rect[1] + 44), 'Row      12        34', fontsize=8)
        page.blocks.append(h.model_block('A distinct synthetic table', f'table{i}', 'table', rect))
    page.text('A separate closing explanation follows both objects.', 'after', y=325)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    tables = [rec for rec in result['images'].values() if rec['kind'] == 'table']
    assert len(tables) == 2
    boxes = sorted((rec['bbox'] for rec in tables), key=lambda b: b[1])
    assert boxes[0][3] < boxes[1][1], 'each table owns its distinct physical source object'
    for box, source in zip(boxes, rectangles):
        assert box[0] <= source[0] and box[1] <= source[1] and box[2] >= source[2] and box[3] >= source[3]
    assert sum(n.tag.endswith('}figure') for n in h.body_nodes(docs)) == 2
