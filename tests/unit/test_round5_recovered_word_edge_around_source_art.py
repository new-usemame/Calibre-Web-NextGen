"""SOURCE-free omitted body recovery around art cannot split a printed word.

A lower original prose row is omitted by the locked reader, then independently
recovered after a caption. Exact printed endpoints and unindented opening can
prove its relationship to the preceding body despite its initial OTHER role.
Terminal text, source indentation, headings and notes remain boundaries.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_s = importlib.util.spec_from_file_location('round5_recovered_word_float', _helper)
h = importlib.util.module_from_spec(_s)
_s.loader.exec_module(h)
pytestmark = pytest.mark.unit


@pytest.mark.parametrize('barrier', ['none', 'context', 'indented', 'terminal', 'heading', 'footnote'])
def test_recovered_source_word_continuation_moves_before_art_and_caption_only_with_proof(tmp_path, monkeypatch, barrier):
    p = h.InkPage()
    top = 'The quiet workers kept several careful specimens well-'
    bottom = 'preserved while the later observations stayed readable.'
    if barrier == 'terminal':
        top = 'The quiet workers completed their ordinary observations.'
    p.text(top, 'upper', x=22, y=55, size=7)
    p.figure()
    if barrier == 'context':
        # Connected original art reaches into the prose territory. It must stay
        # available in a whole-source context rather than lose its upper extent.
        p.page.draw_line((120, 100), (120, 45), width=2, color=(0, 0, 0))
    x = 36 if barrier == 'indented' else 22
    p.page.insert_text((x, 230), bottom, fontsize=7)
    if barrier in ('heading', 'footnote'):
        p.blocks.append(h.model_block(bottom, 'lower', barrier, [x-1, 219, 282, 234],
                                      marker='*' if barrier == 'footnote' else ''))
    # The ordinary aligned row establishes the actual printed column origin;
    # it is a separate terminal paragraph, never part of the recovered word.
    later = 'A separate authored paragraph begins after this account.'
    p.text(later, 'later', x=22, y=280, size=6)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    texts = h.body_texts(docs)
    if barrier in ('none', 'context'):
        expected = top + bottom
        assert expected in texts, 'the source-proved recovered word/sentence stays continuous before the floating source object'
        assert sum(expected in text for text in texts) == 1
        assert later in texts and len(texts) == 2
        nodes = h.body_nodes(docs)
        complete = next(i for i, node in enumerate(nodes) if h.plain(node) == expected)
        art = next(i for i, node in enumerate(nodes) if node.tag.endswith('}figure'))
        assert complete < art, 'figure or original-context control stays outside the continuous word'
        if barrier == 'context':
            assert result['degraded_figure_count'] == 1
            assert nodes[art].find('.//x:a', h.NS) is not None
    else:
        assert all(top + bottom not in text for text in texts), 'a structural or printed paragraph barrier is not promoted from lexical similarity'
        assert any(top in text for text in texts)
        if barrier == 'footnote':
            _, notes = h.refs_and_notes(docs)
            assert any(bottom in ''.join(note.itertext()) for _, note in notes)
            assert all(bottom not in text for text in texts)
    caps = [h.plain(node) for node in h.body_nodes(docs) if node.get('class') == 'caption']
    assert caps == ['A connected synthetic diagram.']
