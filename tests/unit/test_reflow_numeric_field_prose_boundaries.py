"""Dates and field-like codes do not alone prove a source table lane.

These constructed prose controls exercise the figure detector's real prose
obstacles. Only an independently measured repeated field territory may stop
occupying prose space; incidental codes in ordinary sentences must stay live.
The positive controls below require repeated measured field lanes and retain
the same candidate and glyph coverage obligations.
"""
from dataclasses import asdict
import pytest
from cps.services.reflow import extract, skeleton

pytestmark = pytest.mark.unit


def source_rows(texts, *, tops=None, lefts=None, heights=None, scan=True):
    tops = tops or [100 + 18*i for i in range(len(texts))]
    lefts = lefts or [40]*len(texts)
    heights = heights or [11]*len(texts)
    blocks, lines = [], []
    for i, (text, top, left, height) in enumerate(zip(texts, tops, lefts, heights)):
        box = (left, top, left+130, top+height)
        line = extract.Line([extract.Span(text, height, 'body', 0, box)], box)
        lines.append(line)
        blocks.append(extract.Block(i, box, [line]))
    images = [extract.Image((0, 0, 600, 700), 1)] if scan else []
    raw = extract.RawPage(0, 600, 700, blocks, images=images)
    return raw, lines


CASES = [
    pytest.param(['R2 - 1/2/2020'], {}, id='isolated-short-code-and-date'),
    pytest.param(['R2 - 1/2/2020', 'R3 - 2/3/2020'], {}, id='two-neighbor-fields-do-not-prove-table'),
    pytest.param(['Record R2 for 1/2/2020.', 'Record R3 for 2/3/2020.',
                  'Record R4 for 3/4/2020.'], {}, id='lexical-prose-with-code-and-date'),
    pytest.param(['We go by R2 on 1/2/2020.', 'We go by R3 on 2/3/2020.',
                  'We go by R4 on 3/4/2020.'], {}, id='short-lexical-words-are-still-prose'),
    pytest.param(['WE GO BY R2 ON 1/2/2020.', 'WE GO BY R3 ON 2/3/2020.',
                  'WE GO BY R4 ON 3/4/2020.'], {}, id='uppercase-short-lexical-words-are-still-prose'),
    pytest.param(['R2 - 1/2/2020', 'R3 - 2/3/2020', 'R4 - 3/4/2020'],
                 {'tops': [100, 300, 500]}, id='three-widely-separated-fields'),
    pytest.param(['R2 - 1/2/2020', 'R3 - 2/3/2020', 'R4 - 3/4/2020'],
                 {'lefts': [40, 240, 440]}, id='three-distinct-horizontal-lanes'),
    pytest.param(['R2 - 1/2/2020', 'R3 - 2/3/2020', 'R4 - 3/4/2020'],
                 {'heights': [6, 10, 16]}, id='three-incompatible-measured-type-heights'),
    pytest.param(['R2 - 1/2/2020', 'R3 - 2/3/2020', 'R4 - 3/4/2020'],
                 {'scan': False}, id='native-page-does-not-use-scan-field-veto'),
]


@pytest.mark.parametrize('texts,geometry', CASES)
def test_incidental_numeric_fields_keep_original_prose_occupied(texts, geometry):
    raw, lines = source_rows(texts, **geometry)
    before = asdict(raw)
    kept = [(block, block.lines) for block in raw.text_blocks]
    rows, _, _ = skeleton._prose_rows(kept, raw, skeleton.BookStyle(body_size=12))
    occupied = [box for _, _, boxes in rows for box in boxes]
    assert all(line.bbox in occupied for line in lines), (
        'Ordinary prose must keep blocking source-figure growth', texts, occupied)
    assert asdict(raw) == before
    assert all(line is raw.blocks[i].lines[0] for i, line in enumerate(lines))



def test_three_aligned_neighboring_code_dash_date_fields_are_measured_as_table_ink():
    raw, lines = source_rows(['R2 - 1/2/2020', 'R3 - 2/3/2020', 'R4 - 3/4/2020'])
    before = asdict(raw)
    rows, _, _ = skeleton._prose_rows([(b, b.lines) for b in raw.text_blocks],
        raw, skeleton.BookStyle(body_size=12))
    assert not rows, 'A genuine measured field run must permit source-table figure coverage'
    assert asdict(raw) == before
    assert all(line is raw.blocks[i].lines[0] for i, line in enumerate(lines))
