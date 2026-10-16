"""Printed source rules separate close notes and unnumbered continuations."""
import pytest
from cps.services.reflow import extract, skeleton

pytestmark = pytest.mark.unit


def source_fixture(continuation=False, boundary=None):
    def line(text, size, box):
        return extract.Line([extract.Span(text, size, 'Times', 0, box)], box)

    rows = [line('woodworking, our crew now makes benches and new drawers', 11.5,
                 (54, 163.79, 408, 179.38)),
            line('sanding or drilling or varnishing or timber-cutting', 11.5,
                 (54, 179.19, 408, 194.53))]
    if continuation:
        rows.append(line('A prior printed note continues with further explanation.',
                         9.68, (54, 206.27, 408, 218.43)))
        opening_box = (58.5, 230.27, 408, 242.43)
        opening = extract.Line([
            extract.Span('169', 5.3, 'Times', 0, (58.5, 230.27, 68, 236)),
            extract.Span(' Another original printed note.', 9.68, 'Times', 0,
                         (72, 230.8, 408, 242.43))], opening_box)
        if boundary in ('no_raised_anchor', 'short_rule_no_anchor'):
            opening.spans = [extract.Span('169 Another original printed note.',
                                         9.68, 'Times', 0, opening_box)]
        rows.append(opening)
    else:
        opening_box = (58.5, 206.27, 408, 218.43)
        rows.append(extract.Line([
            extract.Span('162', 5.3, 'Times', 0, (58.5, 206.27, 68, 212)),
            extract.Span(' Original printed footnote.', 9.68, 'Times', 0,
                         (72, 206.8, 408, 218.43))], opening_box))
    rows.append(line('Its source-owned explanation continues here.',
                     11.5 if boundary == 'body_below' else 9.68,
                     (54, 254.5, 408, 266.53)))
    raw = extract.RawPage(0, 470, 680,
                         [extract.Block(i, r.bbox, [r]) for i, r in enumerate(rows)])

    class Probe:
        def source_ink_bounds(self, box):
            if boundary == 'no_rule':
                return None
            if boundary == 'thick_rule':
                return (54, 195.5, 408, 204)
            return (54, 201.29, 408 if continuation and boundary not in
                    ('short_rule', 'short_rule_no_anchor') else 127, 202.09)

        ink_bounds = source_ink_bounds

    return raw, Probe()


@pytest.mark.parametrize('boundary', [None, 'no_rule', 'thick_rule', 'body_below'])
def test_close_raised_footnote_needs_independent_printed_rule(boundary):
    raw, probe = source_fixture(boundary=boundary)
    regions = skeleton.opaque_page_regions(raw, skeleton.BookStyle(body_size=11.5), probe)
    notes = [r for r in regions if r.kind == 'artwork' and r.reason == 'ocr_uncertain_note']
    assert bool(notes) == (boundary is None)
    if notes:
        assert notes[0].lines == [b.lines[0] for b in raw.blocks[2:]]
        body, parsed = skeleton._split_off_notes(raw, skeleton.BookStyle(body_size=11.5),
                                                skeleton.PageSkeleton(0, 470, 680), probe)
        assert [n.number for n in parsed] == [162]
        assert 'timber-cutting' in ' '.join(b.text for b in body)
        assert 'Original printed footnote' not in ' '.join(b.text for b in body)


@pytest.mark.parametrize('boundary', [None, 'short_rule', 'no_rule', 'thick_rule', 'body_below',
                                    'no_raised_anchor', 'short_rule_no_anchor'])
def test_unnumbered_continuation_needs_printed_rule_and_later_raised_anchor(boundary):
    raw, probe = source_fixture(continuation=True, boundary=boundary)
    regions = skeleton.opaque_page_regions(raw, skeleton.BookStyle(body_size=11.5), probe)
    notes = [r for r in regions if r.kind == 'artwork' and r.reason == 'ocr_uncertain_note']
    assert bool(notes) == (boundary in (None, 'short_rule'))
    if notes:
        assert notes[0].lines == [b.lines[0] for b in raw.blocks[2:]]
        body, parsed = skeleton._split_off_notes(raw, skeleton.BookStyle(body_size=11.5),
                                                skeleton.PageSkeleton(0, 470, 680), probe)
        assert parsed[0].number is None
        assert 'prior printed note' in parsed[0].text
        assert 'prior printed note' not in ' '.join(b.text for b in body)
        assert [n.number for n in parsed[1:]] == [169]
