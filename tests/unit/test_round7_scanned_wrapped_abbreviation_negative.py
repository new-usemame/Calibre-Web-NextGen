"""Concrete post-C51-writer negative, frozen before first execution."""
import copy
import importlib.util
from pathlib import Path

from cps.services.reflow import vision_pipeline as vp

s = importlib.util.spec_from_file_location('scanned_abbreviation_fixture',
    Path(__file__).with_name('test_round7_scanned_source_paragraph_ownership.py'))
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)
h = c.h


def test_original_wrapped_abbreviation_with_inset_row_is_not_a_new_paragraph(tmp_path, monkeypatch):
    p = h.InkPage()
    lines = ['The traveller studied the panel prepared by Dr.',
             'Rowan during the quiet evening beside the river',
             'while the careful keeper explained the narrow route',
             'and completed the same uninterrupted account.']
    for index, line in enumerate(lines):
        p.page.insert_text((39 if index == 1 else 24, 50 + index * 14), line, fontsize=9)
    text = ' '.join(lines)
    p.blocks = [h.model_block(text, 'body', 'body', [23, 38, 282, 97])]
    original = copy.deepcopy(p.blocks)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert p.blocks == original
    c.invariant(report, docs, [text])
    assert c.lexical(docs) == [text], 'An abbreviation at a wrapped physical row foot is not an authored sentence or paragraph ending'
