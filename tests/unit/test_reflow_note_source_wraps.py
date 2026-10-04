"""Note joins must retain hyphens owned by immutable source spans."""
from collections import Counter

import pytest
from cps.services.reflow import assemble, extract, skeleton


def line(*spans):
    return extract.Line(spans=list(spans), bbox=(0, 0, 100, 10))


def span(text, **flags):
    return extract.Span(text=text, size=10, font='source', flags=0,
                        bbox=(0, 0, 100, 10), **flags)


@pytest.mark.parametrize('protection', ['transcription_uncertain', 'encoding_unresolved'])
@pytest.mark.parametrize('hyphen', ['-', '\u00ad'])
def test_note_join_preserves_source_owned_hyphen(protection, hyphen):
    """Drive note assembly; flattening away source protection must fail."""
    region = skeleton.Region('note', number=7, lines=[
        line(span('7 A '), span('frag' + hyphen, **{protection: True}), span(' ')),
        line(span('ment remains.'))])
    text = assemble.note_text(region, vocab={'fragment'})
    assert text == 'A frag' + hyphen + 'ment remains.'
    source = Counter(['A', 'frag', 'ment', 'remains'])
    note = assemble.Note(num=7, text=text, pno=0)
    assert assemble.check_conservation(source, [], [note], []).ok
    for changed in ['A fragment remains.', 'A frag-ment.', 'A frag-ment extra remains.']:
        note.text = changed
        assert not assemble.check_conservation(source, [], [note], []).ok


@pytest.mark.parametrize('left,right,vocab,expected', [
    ([span('frag-')], 'ment.', {'fragment'}, 'fragment.'),
    ([span('frag', transcription_uncertain=True), span('-')], 'ment.', {'fragment'}, 'fragment.'),
    ([span('frag-')], 'ment.', set(), 'frag-ment.'),
    ([span('Sun-')], 'Moon.', {'sunmoon'}, 'Sun-Moon.'),
    ([span('ordinary')], 'continuation.', set(), 'ordinary continuation.'),
])
def test_note_join_keeps_neighboring_authorized_behavior(left, right, vocab, expected):
    region = skeleton.Region('note', lines=[line(*left), line(span(right))])
    assert assemble.note_text(region, vocab=vocab) == expected
