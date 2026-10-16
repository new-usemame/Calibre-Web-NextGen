"""SOURCE-free exact optical heading rows cannot own neighbouring BODY words."""
import importlib.util
from pathlib import Path
import pytest
import pymupdf
from cps.services.reflow import vision_pipeline

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_spec = importlib.util.spec_from_file_location('round5_exact_heading_ink', _helper)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
pytestmark = pytest.mark.unit


def test_two_heading_rows_union_never_appends_body_on_shared_last_row(tmp_path, monkeypatch):
    p = h.InkPage()
    title_rows = ['A careful comparison of living forms reveals many distinctions in', 'habitat.-For']
    body_rows = ['these wooden brackets fit tightly beside our tray and', 'ordinary specimens remain legible throughout every following row.', 'The paragraph finishes with its own source words intact.']
    for line, y in zip(title_rows, [70, 84]):
        p.page.insert_text((24, y), line, fontsize=7)
    for line, x, y in zip(body_rows, [72, 24, 24], [84, 98, 112]):
        assert x + pymupdf.get_text_length(line, fontsize=7) < h.W - 15
        p.page.insert_text((x, y), line, fontsize=7)
    p.blocks.append(h.model_block(' '.join(title_rows), 'title', 'heading', [23, 60, 282, 88]))
    p.blocks.append(h.model_block(' '.join(body_rows), 'prose', 'body', [23, 74, 282, 117]))
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert h.body_texts(docs) == [' '.join(body_rows)]
    headings = [h.plain(n) for n in h.body_nodes(docs) if n.tag.endswith('}h2')]
    assert headings == [' '.join(title_rows)], 'two-row heading union must exclude explicitly corroborated adjacent BODY instances'
