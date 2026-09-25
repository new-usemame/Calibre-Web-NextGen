"""A scanned symbol key keeps printed row associations when OCR cannot read them."""

import pytest

from cps.services.reflow import assemble, extract, skeleton


def _line(text, box, uncertain=False):
    span = extract.Span(text, 10, 'ocr', 0, box, box[3], uncertain=uncertain)
    return extract.Line([span], box)


def _key_page(*, reliable_strip=False, prose=False, second_panel=False):
    lines = [_line('Printed reference', (210, 45, 300, 60))]
    # Recognition has fused several vertically printed marks into tall lines.
    for i in range(3):
        box = (72, 90+i*110, 82, 187+i*110)
        lines.append(_line('unreadable marks', box, uncertain=not reliable_strip))
    for i in range(12):
        y = 94+i*27
        lines.append(_line('Entry '+str(i), (104, y, 158, y+10)))
        lines.append(_line('associated value', (215, y, 300, y+10)))
    if prose:
        lines.append(_line('This ordinary sentence describes the surrounding material in complete prose.',
                           (105, 468, 355, 480)))
    if second_panel:
        lines.append(_line('Independent material', (12, 150, 60, 162)))
    blocks = [extract.Block(i, line.bbox, [line]) for i, line in enumerate(lines)]
    return extract.RawPage(0, 400, 500, blocks,
                           [extract.Image((0, 0, 400, 500), 1)])


def test_uncertain_symbol_key_is_one_primary_source_object_with_complete_rows():
    raw = _key_page()
    skel = skeleton.page_skeleton(raw, skeleton.book_style([raw]))
    book = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
    assert book.conservation.ok
    assert [r.kind for r in skel.regions] == ['artwork', 'figure']
    assert [f['found'] for f in book.figures] == ['uncertain_scan_key_panel']
    x0, y0, x1, y1 = book.figures[0]['bbox']
    assert x0 < 72 and y0 < 45 and x1 > 300 and y1 > 401
    assert len(book.artwork) == 1
    assert all(line.stripped in book.artwork[0]['text']
               for block in raw.text_blocks for line in block.lines)
    assert book.needs_source_evidence(0)


@pytest.mark.parametrize('change', ['reliable_strip', 'prose', 'second_panel'])
def test_symbol_key_fallback_requires_uncertain_marks_and_one_sparse_panel(change):
    raw = _key_page(**{change: True})
    skel = skeleton.page_skeleton(raw, skeleton.book_style([raw]))
    assert 'uncertain_scan_key_panel' not in skel.reasons
