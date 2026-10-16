"""External page-spine ink must not enlarge an authored illustration."""
import importlib.util
from pathlib import Path
import pytest
_spec = importlib.util.spec_from_file_location('gutter_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

@pytest.mark.parametrize('external_gutter', [True, False])
def test_page_spine_strip_excluded_but_authored_thick_border_retained(tmp_path, monkeypatch, external_gutter):
    page = h.InkPage()
    page.text('A complete ordinary introduction precedes the authored diagram.', 'body', y=50)
    rect = page.figure(hint=[50, 105, 240, 160])
    if external_gutter:
        page.page.draw_rect([287, 0, 295, 400], color=(0, 0, 0), fill=(0, 0, 0))
    else:
        page.page.draw_rect([254, 90, 260, 175], color=(0, 0, 0), fill=(0, 0, 0))
    page.text('A separate ordinary conclusion follows the caption.', 'after', y=235)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [x for x in result['images'].values() if x['kind'] == 'figure']
    assert len(figures) == 1
    box = figures[0]['bbox']
    assert box[0] <= rect[0] and box[1] <= rect[1] and box[2] >= rect[2] and box[3] >= rect[3], 'the whole authored figure including its thick border remains present'
    if external_gutter:
        assert box[2] < 287, 'a full-page spine strip outside the source figure cannot be packaged as figure ink'
    assert 'A separate ordinary conclusion follows the caption.' in ' '.join(h.body_texts(docs))
