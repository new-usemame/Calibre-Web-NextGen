"""A scan's footer never owns the other page's caption or diagram label."""
from dataclasses import asdict
import pytest
from cps.services.reflow import extract, skeleton

pytestmark = pytest.mark.unit


def spread(boundary=None):
    def span(text, size, box):
        return extract.Span(text, size, 'ocr', 0, box)

    body = extract.Line([span('The original sentence continues on the next page', 16,
                              (100, 500, 450, 550))], (100, 500, 450, 550))
    damaged = extract.Line([span('©', 3.2, (100, 650, 108, 658)),
                            span(' Original source date.', 10, (110, 650, 450, 664))],
                           (100, 650, 450, 664))
    anchor = extract.Line([span('7', 3.2, (100, 680, 108, 688)),
                           span(' Original reference.', 10, (110, 681, 450, 695))],
                          (100, 680, 450, 695))
    other_x = 195 if boundary == 'no_gutter' else 710
    top = 300 if boundary == 'not_footer' else 710
    mixed = extract.Line([
        span('8', 12 if boundary == 'body_size_marker' else 3.2,
             (100, top, 108, top + 8)),
        span(' Ibid.', 10, (110, top + 1, 180, top + 15)),
        span('Plate caption.', 10, (other_x, top + 1, other_x + 190, top + 15)),
        span('41°17', 5, (other_x + 195, top + 1, other_x + 215, top + 7))],
        (100, top, other_x + 215, top + 15))
    height = 1000 if boundary == 'portrait' else 800
    return extract.RawPage(0, 1000, height, [
        extract.Block(0, body.bbox, [body]),
        extract.Block(1, (100, 650, 450, 695), [damaged, anchor]),
        extract.Block(2, mixed.bbox, [mixed])],
        images=[extract.Image((0, 0, 1000, height), 1)],
        source_geometry={} if boundary == 'native_space' else {'space': 'reading'})


def spans(raw):
    return [asdict(s) for b in raw.blocks for l in b.lines for s in l.spans]


@pytest.mark.parametrize('boundary', [None, 'no_gutter', 'not_footer',
                                    'body_size_marker', 'portrait', 'native_space'])
def test_only_measured_scan_footer_gutter_splits_literal_source_spans(boundary):
    original = spread(boundary)
    before = asdict(original)
    result = skeleton.split_scan_note_columns(original, skeleton.BookStyle(body_size=16))
    assert asdict(original) == before
    assert spans(result) == spans(original), 'every measured literal span must remain exactly once'
    if boundary is None:
        assert result is not original
        assert result.blocks[-2].bbox[2] < result.blocks[-1].bbox[0]
        assert result.blocks[-2].text == '8 Ibid.'
        assert result.blocks[-1].text == 'Plate caption.41°17'
    else:
        assert result is original


def test_damaged_footer_stays_unnumbered_and_unrelated_caption_stays_outside_notes():
    original = spread()
    raw = skeleton.split_scan_note_columns(original, skeleton.BookStyle(body_size=16))
    body, notes = skeleton._split_off_notes(raw, skeleton.BookStyle(body_size=16),
                                           skeleton.PageSkeleton(0, 1000, 800))
    assert [n.number for n in notes] == [None, 7, 8]
    assert notes[0].reason == 'damaged_note_label' and notes[0].uncertain
    assert 'Original source date' in notes[0].text
    assert 'Plate caption' not in ' '.join(n.text for n in notes)
    assert '41°17' not in ' '.join(n.text for n in notes)
    assert 'Plate caption' in ' '.join(b.text for b in body)
    assert 'Original source date' not in ' '.join(b.text for b in body)
    assert len([s for b in body for l in b.lines for s in l.spans]) + len(
        [s for n in notes for l in n.lines for s in l.spans]) == len(spans(original))
