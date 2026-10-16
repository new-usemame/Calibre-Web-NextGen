"""SOURCE-free actual EPUB: short heading cannot borrow a corroborated body row.

Locked independent optical recognition may damage one heading word. Authored
model heading text remains correct; explicit physical BODY instances own their
row even when a coarse heading hint spans that row and later paragraph lines.
"""
import importlib.util
from pathlib import Path

import pytest
from cps.services.reflow import vision_pipeline

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_spec = importlib.util.spec_from_file_location('round5_shared_row_ink', _helper)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
pytestmark = pytest.mark.unit


@pytest.mark.parametrize('damaged_optical', [True, False])
def test_heading_and_explicit_body_share_row_without_lexical_theft(tmp_path, monkeypatch, damaged_optical):
    p = h.InkPage()
    title = 'A STUDY OF SHELLS'
    first = 'The patient collector finds eight small specimens beside the shore.'
    continuation = 'Every later printed row belongs to this same ordinary paragraph.'
    repeated = 'A STUDY OF SHELLS is an authored phrase repeated within the body.'
    p.page.insert_text((24, 70), title, fontsize=7)
    p.page.insert_text((102, 70), first, fontsize=6)
    p.page.insert_text((24, 84), continuation, fontsize=6)
    p.page.insert_text((24, 198), repeated, fontsize=6)
    p.blocks.append(h.model_block(title, 'title', 'heading', [23, 59, 282, 102]))
    p.blocks.append(h.model_block(' '.join([first, continuation]), 'prose', 'body', [23, 60, 282, 89]))
    p.blocks.append(h.model_block(repeated, 'later', 'body', [23, 189, 282, 202]))
    finish = p.finish
    def locked_source():
        image, words, answer = finish()
        if damaged_optical:
            # A single SOURCE recognition error leaves the printed pixels intact.
            damaged = [w for w in words if w['text'] == 'SHELLS' and w['bbox'][1] < 70]
            assert len(damaged) == 1
            damaged[0]['text'] = 'SHELIS'
        return image, words, answer
    p.finish = locked_source
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert h.body_texts(docs) == [' '.join([first, continuation]), repeated], 'all explicit BODY instances survive exactly once'
    headings = [h.plain(n) for n in h.body_nodes(docs) if n.tag.endswith('}h2')]
    assert headings == [title], 'short heading keeps its authored words, never appends the adjacent live body'
