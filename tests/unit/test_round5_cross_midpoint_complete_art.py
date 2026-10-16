"""One SOURCE object spanning a spread midpoint retains both disconnected panels."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_p = Path(__file__).with_name('test_round5_figure_recovery_calibrated.py')
if not _p.exists():
    _p = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round5_figure_recovery.py'
_s = importlib.util.spec_from_file_location('round5_cross_midpoint_ink', _p)
r = importlib.util.module_from_spec(_s); _s.loader.exec_module(r)
h = r.h
pytestmark = pytest.mark.unit


def test_one_raw_figure_crossing_midpoint_keeps_both_original_panels(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'W', 600)
    p = h.InkPage()
    text = 'A live source paragraph precedes the complete paired illustration.'
    p.text(text, 'prose', y=50, size=9)
    for rect in ([180, 80, 260, 180], [340, 80, 420, 180]):
        p.page.draw_rect(rect, color=(0, 0, 1), width=2)
        p.page.draw_line((rect[0], rect[1]), (rect[2], rect[3]), color=(0, 0, 1), width=1)
    p.blocks.append(h.model_block('A paired source illustration', 'paired', 'figure', [170, 70, 430, 190]))
    p.text('Both panels belong to this one illustration.', 'caption', 'caption', x=180, y=210, size=7)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert h.body_texts(docs) == [text]
    r.whole_art_or_context(path, result, docs, 'paired', [180, 80, 420, 180])
