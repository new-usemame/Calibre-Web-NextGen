"""An ordinary footer note can continue without a dedicated NOTES section."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    'round4_footer_turn_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


@pytest.mark.parametrize('continued', [True, False])
def test_footer_note_page_turn_uses_small_source_continuation_not_ordinary_body(tmp_path, monkeypatch, continued):
    first, second = h.InkPage(), h.InkPage()
    first.text(['Ordinary source prose cites a note.',
                'Another full size sentence ends.'], 'body', size=12,
               model='Ordinary source prose cites<n:n7> a note. Another full size sentence ends.')
    opening = ('A small printed footer note starts a careful observation and'
               if continued else 'A small printed footer note ends its observation safely.')
    first.text('7 ' + opening, 'n7', 'footnote', y=330, size=7,
               marker='7', model=opening)
    main = 'Separate ordinary body prose ends safely.'
    second.text(main, 'body', size=12)
    tail = ('further small printed details finish the same note precisely.'
            if continued else 'ordinary full size footer prose stays.')
    second.text(tail, 'tail', y=330, size=7 if continued else 12,
                previous=True)

    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [first, second])
    _, notes = h.assert_exact_returns(docs, 1)
    assert len(notes) == 1, 'one footer note owner7, with an exact reference Return'
    note = notes[0][1]
    assert opening in ''.join(note.itertext())
    body = h.body_texts(docs)
    assert main in body
    source_pages = {int(s.get('data-source-page'))
                    for s in note.findall('.//x:span[@data-source-page]', h.NS)}
    if continued:
        assert tail not in '\n'.join(body), 'small source-proven note continuation must stay outside body flow'
        assert tail in ''.join(note.itertext())
        assert source_pages == {0, 1}, 'merged note retains both physical source-page spans'
    else:
        assert tail in body, 'a completed previous note must not absorb ordinary full-size footer prose'
        assert tail not in ''.join(note.itertext())
        assert source_pages == {0}
