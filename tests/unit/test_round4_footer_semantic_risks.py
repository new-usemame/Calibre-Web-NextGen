"""Independent source-free footer ownership controls; actual EPUB output."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('round4_footer_risk_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_unfinished_labelled_footer_note_retains_single_letter_coefficient_continuation(tmp_path, monkeypatch):
    first, second = h.InkPage(), h.InkPage()
    first.text(['Ordinary readable body prose cites a note.',
                'Another ordinary sentence ends safely.'], 'body', size=12,
               model='Ordinary readable body prose cites<n:n7> a note. Another ordinary sentence ends safely.')
    opening = 'A small source footer defines the variable coefficient'
    first.text('7 ' + opening, 'n7', 'footnote', y=330, size=7,
               marker='7', model=opening)
    second.text(['Separate full size prose occupies the next page.',
                 'Its ordinary explanation finishes safely.'], 'body', size=12)
    second.text('n', 'tail', 'other', x=130, y=375, size=7, previous=True)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [first, second])
    _, notes = h.assert_exact_returns(docs, 1)
    assert len(notes) == 1
    note = notes[0][1]
    pages = {int(s.get('data-source-page')) for s in note.findall('.//x:span[@data-source-page]', h.NS)}
    assert pages == {0, 1}, 'the small source continuation belongs to its labelled footer note, not discarded furniture'
    assert h.plain(note) == '7 ' + opening + ' n', 'the printed label and single-letter coefficient remain exactly in the actual final note'
    assert 'n' not in h.body_texts(docs), 'a carried footer coefficient is not a new body paragraph'


def test_isolated_small_symbol_without_local_body_clearance_is_not_discarded_as_signature(tmp_path, monkeypatch):
    page = h.InkPage()
    page.text(['Readable left prose.', 'Another clear line.', 'The explanation ends.'], 'body', size=12)
    page.page.insert_text((250, 378), 'q', fontsize=6)
    page.blocks.append(h.model_block('A small isolated symbolic illustration', 'symbol', 'figure', [246, 367, 262, 389]))
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [rec for rec in result['images'].values() if rec['kind'] == 'figure']
    assert len(figures) == 1, 'no local prose separation witness means an isolated other-column symbol cannot be silently certified as printer furniture'
    assert len([n for n in h.body_nodes(docs) if n.tag.endswith('}figure')]) == 1
    assert h.body_texts(docs) == ['Readable left prose. Another clear line. The explanation ends.']
