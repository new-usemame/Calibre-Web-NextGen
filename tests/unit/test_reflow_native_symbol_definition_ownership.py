"""Synthetic native symbol definitions retain their authored source role."""
from dataclasses import asdict

import pytest

from cps.services.reflow import extract, skeleton

pytestmark = pytest.mark.unit


def definition_page(tmp_path, *, equality=True, explicit_note=False):
    """Draw procedural ink and supply independently constructed native rows."""
    document = extract.pymupdf.open()
    page = document.new_page(width=600, height=800)
    rows = []
    for index, y in enumerate((550, 600)):
        text = ('Procedural samples remain arranged across the storage shelf. '
                'Printed labels preserve the order of every item.')
        box = (40, y, 500, y+12)
        page.insert_text((40, y+10), text, fontsize=12)
        line = extract.Line([extract.Span(text, 12, 'Helvetica', 0, box,
                                         origin_y=y+10)], box)
        rows.append(extract.Block(index, box, [line]))
    page.draw_line((40, 640), (500, 640), width=.6)
    page.draw_rect((35, 659, 38, 662), color=None, fill=(0, 0, 0))
    words = extract.Span('Selected labels denote stored samples.', 8,
                         'Helvetica', 0, (52, 660, 250, 668), origin_y=666)
    page.insert_text((52, 666), words.text, fontsize=8)
    prefix = []
    if equality:
        page.insert_text((43, 666), '=', fontsize=8)
        prefix.append(extract.Span('=', 8, 'Helvetica', 0,
                                   (43, 660, 48, 668), origin_y=666))
    if explicit_note:
        page.insert_text((43, 658), '1', fontsize=4)
        prefix.insert(0, extract.Span('1', 4, 'Helvetica', 0,
                                    (43, 654, 47, 659), origin_y=658))
    line = extract.Line(prefix+[words], (43 if prefix else 52, 654 if explicit_note else 660, 250, 668))
    rows.append(extract.Block(2, line.bbox, [line]))
    path = tmp_path/'procedural-definitions.pdf'
    document.save(path)
    document.close()
    raw = extract.RawPage(0, 600, 800, rows,
                          images=[extract.Image((0, 0, 600, 800), 1)])
    return raw, line, path


def test_fullsize_native_equality_key_does_not_borrow_unowned_pixel_note_marker(tmp_path):
    raw, target, path = definition_page(tmp_path)
    before = asdict(raw)
    with extract.pymupdf.open(path) as doc:
        probe = extract.ScanPixelProbe(doc, 0,
            mask=[line.bbox for block in raw.text_blocks for line in block.lines])
        result = skeleton.page_skeleton(raw, skeleton.BookStyle(body_size=12),
                                        pixel_probe=probe)
    owners = [region for region in result.regions
              if any(target is line for line in region.lines+region.caption_lines)]
    assert len(owners) == 1
    assert owners[0].kind != 'note' and owners[0].reason != 'ocr_uncertain_note'
    assert asdict(raw) == before
    assert sum(len(region.lines)+len(region.caption_lines) for region in result.regions) == 3


@pytest.mark.parametrize('size_ratio,baseline_shift', [(.9, 0), (1, 0), (1.1, 0), (1, 1)])
def test_fullsize_baseline_native_equality_prefix_blocks_only_unowned_marker_authority(
        tmp_path, size_ratio, baseline_shift):
    raw, target, path = definition_page(tmp_path)
    target.spans[0].size = 8*size_ratio
    target.spans[0].origin_y += baseline_shift
    with extract.pymupdf.open(path) as doc:
        probe = extract.ScanPixelProbe(doc, 0)
        assert skeleton._opaque_unowned_note_marker(target, [target], probe, 12, 8, 40) is None


@pytest.mark.parametrize('change', ['small', 'large', 'raised', 'ocr', 'uncertain',
                                   'encoding_unresolved', 'transcription_uncertain',
                                   'punctuation_uncertain', 'line_transcription_uncertain'])
def test_unproved_or_differently_placed_prefix_cannot_veto_independent_pixel_evidence(tmp_path, change):
    raw, target, path = definition_page(tmp_path)
    prefix = target.spans[0]
    if change == 'small': prefix.size = 8*.899
    elif change == 'large': prefix.size = 8*1.101
    elif change == 'raised': prefix.origin_y -= 1.01
    elif change == 'ocr': prefix.font = 'ocr'
    elif change == 'line_transcription_uncertain': target.transcription_uncertain = True
    else: setattr(prefix, change, True)
    with extract.pymupdf.open(path) as doc:
        probe = extract.ScanPixelProbe(doc, 0)
        assert skeleton._opaque_unowned_note_marker(target, [target], probe, 12, 8, 40)


@pytest.mark.parametrize('explicit_note', [False, True])
def test_unowned_raised_marker_and_explicit_raised_note_proofs_remain_available(tmp_path, explicit_note):
    raw, target, path = definition_page(tmp_path, equality=False, explicit_note=explicit_note)
    with extract.pymupdf.open(path) as doc:
        probe = extract.ScanPixelProbe(doc, 0,
            mask=[line.bbox for block in raw.text_blocks for line in block.lines])
        result = skeleton.page_skeleton(raw, skeleton.BookStyle(body_size=12),
                                        pixel_probe=probe)
    owners = [region for region in result.regions if any(target is line for line in region.lines)]
    assert len(owners) == 1 and owners[0].kind == 'note'
