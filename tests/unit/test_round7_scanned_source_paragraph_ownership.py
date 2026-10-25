"""Frozen source-free raster/model BODY paragraph ownership controls.

Original ink, optical word instances and cached responses are independently
constructed synthetic inputs. Public convert must retain original paragraph
boundaries without changing lexical tokens, note ownership or confidence spans.
"""
import copy
import importlib.util
import re
from collections import Counter
from pathlib import Path

import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp, vision_pixels

spec = importlib.util.spec_from_file_location(
    'scanned_paragraph_original_ink',
    Path(vp.__file__).parents[3] / 'tests/unit/test_round4_defects.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
pytestmark = pytest.mark.unit

PARAS = [
    ['The patient keeper watched the river.',
     'A quiet boat reached the sheltered bank.'],
    ['A young visitor asked for clear directions.',
     'The careful speaker described the woodland path.'],
    ['The keeper replied with a gentle warning.',
     'Every traveller returned before the evening rain.'],
]
NOTE = 'A distinct local note explains the chosen route.'


def fixture(mode='plain', indent=True, separate_model=False):
    p = h.InkPage()
    paragraphs = copy.deepcopy(PARAS)
    model_paragraphs = []
    for index, lines in enumerate(paragraphs):
        y = 50 + index * 42
        x = 39 if indent and index else 24
        for row, text in enumerate(lines):
            p.page.insert_text((x if row == 0 else 24, y + row * 14), text, fontsize=9)
        model_paragraphs.append(' '.join(lines))
    if mode == 'note':
        # A genuine raised original marker and an exact known note owner.
        prefix = PARAS[1][0]
        gx = (39 if indent else 24) + pymupdf.get_text_length(prefix, fontsize=9)
        p.page.insert_text((gx + 1, 88), '*', fontsize=5)
        model_paragraphs[1] = model_paragraphs[1].replace(prefix, prefix + '<n:n1>')
        p.text('* ' + NOTE, 'n1', 'footnote', y=330, size=7, marker='*', model=NOTE)
    for index, text in enumerate(model_paragraphs if separate_model else [' '.join(model_paragraphs)]):
        rect = [23, 38 + index * 42, 282, 69 + index * 42] if separate_model else [23, 38, 282, 151]
        block = h.model_block(text, 'body' + str(index), 'body', rect)
        if mode == 'uncertain':
            # Same original lexical value; explicitly marked model confidence
            # remains attached to its exact occurrence after paragraph repair.
            block['low_confidence'] = [text.split().index('careful')]
        p.blocks.insert(index, block)
    return p, [' '.join(lines) for lines in paragraphs]


def lexical(docs):
    return h.body_texts(docs)


def source_returns(docs):
    controls = [(name, a) for name, root in docs.items()
                for a in root.findall('.//x:a', h.NS)
                if a.get('class') == 'source-disclosure-ref']
    for name, a in controls:
        target_file, target = h.destination(name, a.get('href'))
        nodes = [n for n in docs[target_file].iter() if n.get('id') == target]
        assert len(nodes) == 1
        assert nodes[0].findall('.//x:img', h.NS), 'Original uncertainty evidence must remain visible'
        assert any(h.destination(target_file, back.get('href')) == (name, a.get('id'))
                   for back in nodes[0].findall('.//x:a', h.NS)
                   if back.get('href') and '#' in back.get('href'))
    return controls


def invariant(report, docs, expected):
    actual = ' '.join(lexical(docs))
    assert Counter(actual.split()) == Counter(' '.join(expected).split()), 'Every ordinary original token must occur exactly once'
    assert actual.split() == ' '.join(expected).split(), 'Original lexical reading order is immutable'
    assert set(map(int, report['page_homes'])) == {0}
    assert not [x for x in report.get('degraded_units', []) if x.get('unit_kind') != 'note_marker'], 'Proved ordinary BODY may not be replaced with raster'


@pytest.mark.parametrize('mode', ['plain', 'note', 'uncertain'])
def test_merged_model_BODY_restores_three_indented_original_paragraphs(tmp_path, monkeypatch, mode):
    p, expected = fixture(mode)
    original = copy.deepcopy(p.blocks)
    if mode == 'note':
        # No character-OCR fixture or reader answer is allowed to invent a
        # display label; the existing unproved marker disclosure is retained.
        monkeypatch.setattr(vision_pixels, 'raised_markers', lambda *args: [])
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert p.blocks == original, 'Publication must not mutate the cached response'
    invariant(report, docs, expected)
    if mode == 'note':
        refs, notes = h.assert_exact_returns(docs, 1)
        assert refs[0][1].get('href').endswith('#v4-note-0-n1')
        assert len(notes) == 1 and NOTE in h.plain(notes[0][1])
        assert len(source_returns(docs)) == 1
        assert len(report.get('note_marker_disclosures', [])) == 1
    if mode == 'uncertain':
        block=next(b for b in report['pages'][0]['blocks'] if b['id']=='body0-p2')
        assert block['published_tokens'][8]=='careful'
        assert source_returns(docs)==[], 'A positively pixel-settled unchanged word needs no unresolved note'
        assert block['verified_unchanged']==[8] and block['marked']==[]
    assert lexical(docs) == expected, 'One model block cannot erase three independently printed paragraph starts'


@pytest.mark.parametrize('pitch', [14, 27])
def test_true_wrapped_scanned_paragraph_remains_one_even_when_sentences_end(tmp_path, monkeypatch, pitch):
    p = h.InkPage()
    lines = ['The careful keeper watched a quiet boat.',
             'A patient visitor followed the narrow path.',
             'Every traveller returned before the rain.']
    for index, line in enumerate(lines):
        p.page.insert_text((24, 50 + index * pitch), line, fontsize=9)
    text = ' '.join(lines)
    p.blocks = [h.model_block(text, 'body', 'body', [23, 38, 282, 55 + 2 * pitch])]
    original = copy.deepcopy(p.blocks)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert p.blocks == original
    invariant(report, docs, [text])
    assert lexical(docs) == [text], 'Terminal punctuation on a wrapped row alone is not a paragraph boundary'


def test_already_separate_model_paragraphs_keep_original_owners(tmp_path, monkeypatch):
    p, expected = fixture(separate_model=True)
    original = copy.deepcopy(p.blocks)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert p.blocks == original
    invariant(report, docs, expected)
    assert lexical(docs) == expected

