"""Independent constructed boundaries for original marker/text association.

These controls are not authored source witnesses. They test ambiguity and
geometry without allowing pair lookup alone to certify a printed-note zone.
The separate physical page tests bind the actual source pixels and note roles.
"""
from collections import Counter
from dataclasses import asdict
import pytest
from cps.services.reflow import extract, skeleton

pytestmark = pytest.mark.unit


def marker(text, baseline=650, x=40):
    box = (x, baseline-5, x+3, baseline)
    return extract.Line([extract.Span(text, 5, 'native', 0, box,
        origin_y=baseline)], box)


def lexical(text='Original citation text.', baseline=650, x=59, size=8):
    box = (x, baseline-8, x+190, baseline)
    return extract.Line([extract.Span(text, size, 'native', 0, box,
        origin_y=baseline)], box)


def block(lines):
    return extract.Block(0, (min(l.bbox[0] for l in lines), min(l.bbox[1] for l in lines),
        max(l.bbox[2] for l in lines), max(l.bbox[3] for l in lines)), lines)


@pytest.mark.parametrize('boundary', ['two-markers-share-one-text', 'two-texts-tie-one-marker',
                                     'distant-word-gap', 'wrong-source-baseline'])
def test_geometry_cannot_invent_a_unique_original_source_note_pair(boundary):
    first = marker('1')
    text = lexical()
    if boundary == 'two-markers-share-one-text':
        lines = [first, marker('2', x=45), lexical(x=65)]
    elif boundary == 'two-texts-tie-one-marker':
        lines = [first, text, lexical('Another citation text.', x=60)]
    elif boundary == 'distant-word-gap':
        lines = [first, lexical(x=180)]
    else:
        lines = [first, lexical(baseline=700)]
    source = block(lines)
    before = asdict(source)
    assert not skeleton._source_note_marker_pairs(source), boundary
    assert asdict(source) == before


@pytest.mark.parametrize('word_size', [12, 14], ids=['ordinary-body-type', 'reliable-font-inflation'])
def test_geometric_marker_pair_does_not_certify_reliable_ordinary_prose_as_a_footer(word_size):
    # The smaller source box does not override reliable body-size lexical type.
    # No source rule, physical footer proof or context teacher is supplied.
    source = block([marker('1'), lexical('Ordinary reliable prose remains in the body.', size=word_size)])
    above = lexical('Earlier ordinary prose occupies the same authored column.',
        baseline=500, x=40, size=12)
    prior = extract.Block(1, above.bbox, [above])
    raw = extract.RawPage(0, 600, 800, [prior, source])
    before = asdict(raw)
    pairs = skeleton._source_note_marker_pairs(source)
    assert pairs == {id(source.lines[0]): source.lines[1]}, 'Exercise valid pairing before the separate role gate'
    sk = skeleton.PageSkeleton(0, 600, 800)
    kept, notes = skeleton._split_off_notes(raw, skeleton.BookStyle(body_size=12), sk)
    assert not notes, 'A unique pair is geometry, not independent printed-note role proof'
    assert any(any(line is source.lines[1] for line in b.lines) for b in kept)
    assert asdict(raw) == before


def test_unique_same_baseline_pair_retains_both_original_rows():
    label, text = marker('1'), lexical()
    source = block([label, text])
    before = asdict(source)
    assert skeleton._source_note_marker_pairs(source) == {id(label): text}
    assert asdict(source) == before


def test_geometry_pairs_two_original_notes_without_changing_the_extracted_source_rows():
    first, second = marker('1'), marker('2', baseline=662)
    text1, text2 = lexical('Workshop Drawings original citation.'), lexical('Folderbook original citation.', baseline=662)
    source = block([first, second, text1, text2])
    before = asdict(source)
    pairs = skeleton._source_note_marker_pairs(source)
    assert pairs == {id(first): text1, id(second): text2}
    notes = skeleton._notes_in_block(source, [])
    assert len(notes) == 2
    assert notes[0].lines == [first, text1] and notes[1].lines == [second, text2]
    assert Counter(id(line) for note in notes for line in note.lines) == Counter(id(line) for line in source.lines)
    assert asdict(source) == before
