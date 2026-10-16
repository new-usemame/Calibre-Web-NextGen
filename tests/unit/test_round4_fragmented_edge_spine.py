"""Independent original-pixel fragmented edge-spine controls, source-free.

The real scanned converter and image package run against fixed optical words.
Sparse scan evidence never grants permission to erase authored illustration ink.
"""
import importlib.util
from pathlib import Path
import pytest

helper_path = Path(__file__).with_name('test_round4_defects.py')
if not helper_path.exists():
    helper_path = Path(__file__).parents[1] / 'test_round4_defects.py'
spec = importlib.util.spec_from_file_location('fragmented_spine_helpers', helper_path)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def source_page():
    page = h.InkPage()
    page.text('A complete ordinary introduction precedes the authored diagram.', 'body', y=50)
    rect = page.figure(hint=[50, 105, 240, 160])
    page.text('A separate ordinary conclusion follows the caption.', 'after', y=235)
    return page, rect


@pytest.mark.parametrize('gray', [0.55, 0.72])
def test_aligned_separated_page_edge_spine_not_packaged_as_figure(tmp_path, monkeypatch, gray):
    page, rect = source_page()
    # Five separate aligned bands cover broad original-page context, above,
    # through and below the authored figure. No column is 80% dark/full-height.
    # Every band is outside ALL original figure/table hints near a true edge.
    for y0, y1 in [(8, 38), (70, 110), (135, 180), (265, 305), (330, 390)]:
        page.page.draw_rect([291, y0, 294, y1], color=(gray,)*3, fill=(gray,)*3)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [x for x in result['images'].values() if x['kind'] == 'figure']
    assert len(figures) == 1
    box = figures[0]['bbox']
    assert box[0] <= rect[0] and box[1] <= rect[1] and box[2] >= rect[2] and box[3] >= rect[3], 'whole authored connected diagram remains'
    assert 'A separate ordinary conclusion follows the caption.' in ' '.join(h.body_texts(docs))
    assert box[2] < 290, 'aligned fragmented edge spine with page-context evidence must stay outside the packaged figure'


@pytest.mark.parametrize('authored', ['fragmented_legend', 'connected_art', 'isolated_line'])
def test_authored_or_unsupported_edge_ink_retained(tmp_path, monkeypatch, authored):
    page, rect = source_page()
    if authored == 'fragmented_legend':
        # Same near-edge geometry, but ALL seed ownership protects the authored
        # diagram's legend. The original seed explicitly owns its outer ink.
        page.blocks[1]['bbox'] = h.model_block('', 'unused', 'figure', [35, 88, 295, 180])['bbox']
        for y0, y1 in [(95, 110), (125, 145), (160, 175)]:
            page.page.draw_rect([291, y0, 294, y1], color=(.55,)*3, fill=(.55,)*3)
    elif authored == 'connected_art':
        # Original connected figure ink reaches the edge through a horizontal
        # authored branch. Narrow outer columns alone cannot prove scan damage.
        page.page.draw_line((260, 130), (294, 130), color=(.55,)*3, width=2)
        page.page.draw_rect([291, 95, 294, 173], color=(.55,)*3, fill=(.55,)*3)
    else:
        # One unowned narrow line lacks repeated page-context corroboration.
        page.page.draw_rect([291, 110, 294, 155], color=(.55,)*3, fill=(.55,)*3)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [x for x in result['images'].values() if x['kind'] == 'figure']
    assert len(figures) == 1
    box = figures[0]['bbox']
    assert box[0] <= rect[0] and box[1] <= rect[1] and box[2] >= rect[2] and box[3] >= rect[3]
    assert 'A separate ordinary conclusion follows the caption.' in ' '.join(h.body_texts(docs))
    assert box[2] >= 293, 'authored seed/connected artwork or unsupported isolated edge line must not be removed'


@pytest.mark.parametrize('bands', [
    [(8, 38), (70, 110), (135, 180), (265, 305), (330, 390)],
    [(1, 296), (320, 355), (371, 399)],
])
def test_fragmented_spread_leaf_edge_spine_context(tmp_path, monkeypatch, bands):
    old_width = h.W
    h.W = 600
    try:
        page, rect = source_page()
        for y0, y1 in bands:
            page.page.draw_rect([291, y0, 294, y1], color=(.55,)*3, fill=(.55,)*3)
        _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
        figures = [x for x in result['images'].values() if x['kind'] == 'figure']
        assert len(figures) == 1
        box = figures[0]['bbox']
        assert box[0] <= rect[0] and box[1] <= rect[1] and box[2] >= rect[2] and box[3] >= rect[3]
        assert 'A separate ordinary conclusion follows the caption.' in ' '.join(h.body_texts(docs))
        assert box[2] < 290, 'disconnected spine near an original scanned-leaf edge must stay outside the authored figure, even with aggregate dark density above80percent'
    finally:
        h.W = old_width
