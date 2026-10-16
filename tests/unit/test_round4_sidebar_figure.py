"""Complete sidebar illustration ink must remain disjoint from live prose."""
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest
from PIL import Image

_spec = importlib.util.spec_from_file_location(
    'round4_sidebar_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


@pytest.mark.parametrize('side', ['left', 'right'])
def test_sidebar_figure_keeps_complete_corner_ink_and_live_neighbor_prose(tmp_path, monkeypatch, side):
    page = h.InkPage()
    box = [35, 95, 100, 195] if side == 'left' else [200, 95, 265, 195]
    prose_x = 135 if side == 'left' else 24
    lines = ['Ruby prose remains live beside',
             'a separate complete illustration.',
             'Every printed word stays readable.',
             'The final sentence ends safely.']
    page.text(lines, 'neighbor', x=prose_x, y=132, size=7, color=(1, 0, 0))
    # A small source/model hint inside one connected object must discover its
    # whole extent, even though the neighboring prose straddles its center.
    page.page.draw_rect(box, color=(0, 0, 0), fill=(.85, .85, .85), width=2)
    page.page.draw_line((box[0], box[1]), (box[2], box[3]), color=(0, 0, 0), width=2)
    for x in (box[0], box[2] - 7):
        for y in (box[1], box[3] - 7):
            page.page.draw_rect([x, y, x + 7, y + 7], color=(0, 0, 1), fill=(0, 0, 1))
    page.blocks.insert(0, h.model_block(
        'A synthetic complete sidebar illustration', 'sidebar', 'figure',
        [box[0] + 18, 120, box[2] - 18, 140]))

    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [(src, rec) for src, rec in result['images'].items() if rec['kind'] == 'figure']
    assert len(figures) == 1
    src, rec = figures[0]
    assert rec['bbox'][0] <= box[0] and rec['bbox'][1] <= box[1]
    assert rec['bbox'][2] >= box[2] and rec['bbox'][3] >= box[3]
    assert h.body_texts(docs) == [' '.join(lines)]
    assert len([n for n in h.body_nodes(docs) if n.tag.endswith('}figure')]) == 1
    with zipfile.ZipFile(path) as archive:
        member = next(n for n in archive.namelist() if n.endswith(src))
        image = Image.open(io.BytesIO(archive.read(member))).convert('RGB')
    blue = [(x, y) for y in range(image.height) for x in range(image.width)
            if (lambda p: p[2] > 130 and p[0] < 100 and p[1] < 100)(image.getpixel((x, y)))]
    assert blue and min(x for x, y in blue) < image.width * .15
    assert max(x for x, y in blue) > image.width * .85
    assert min(y for x, y in blue) < image.height * .1
    assert max(y for x, y in blue) > image.height * .9
    assert not any(r > 130 and g < 90 and b < 90 for r, g, b in image.getdata()), 'sidebar crop must exclude neighboring ruby prose'
