"""Repeated short referenced notes retain owners; genuine furniture leaves flow."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    'round4_repeat_note_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


@pytest.mark.parametrize('repeated_note', [True, False])
def test_short_referenced_footer_notes_keep_distinct_labels_and_exact_returns(tmp_path, monkeypatch, repeated_note):
    pages = []
    for number in (7, 8):
        page = h.InkPage()
        page.reference(number=number, raised=True)
        # The physical raised number is present. This local schema reference
        # must remain bound even if another page has identical note prose.
        page.blocks[0]['text'] = page.blocks[0]['text'].replace(f'({number})', '<n:note>')
        text = ('A brief synthetic note names one detail.' if repeated_note else
                f'A distinct synthetic note names detail {number}.')
        page.page.draw_line((24, 350), (130, 350), width=.5)
        page.text(f'{number} ' + text, 'note', 'footnote', y=366, size=7,
                  marker=str(number), model=text)
        if not repeated_note:
            page.text('A recurring page footer', 'footer', 'other',
                      x=95, y=388, size=7)
        pages.append(page)

    _, _, docs = h.convert_ink(tmp_path, monkeypatch, pages)
    refs, notes = h.assert_exact_returns(docs, 2)
    assert len(notes) == 2, 'two explicitly marked source notes retain distinct owners'
    assert {''.join(ref.itertext()) for _, ref in refs} == {'7', '8'}
    targets = {h.destination(name, ref.get('href')) for name, ref in refs}
    assert len(targets) == 2
    assert {int(s.get('data-source-page'))
            for _, note in notes
            for s in note.findall('.//x:span[@data-source-page]', h.NS)} == {0, 1}
    assert all('synthetic note' not in text for text in h.body_texts(docs))
    if not repeated_note:
        assert 'A recurring page footer' not in '\n'.join(h.body_texts(docs))
        assert all('A recurring page footer' not in ''.join(note.itertext()) for _, note in notes)
