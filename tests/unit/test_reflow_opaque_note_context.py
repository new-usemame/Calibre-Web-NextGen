"""Proved note territory and proved numeric identity are separate source facts.

Recognition damage must retain an existing printed note's pixels in the final
notes lane. It must neither reclaim that note as body art nor invent its number.
No source note proof leaves the uncertain source material in the body lane.
"""
from dataclasses import asdict
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble, build_epub, extract, native_text, reading_flow, skeleton
pytestmark = pytest.mark.unit


def prepared(mixed=False, carrier=False, unproved=False, neighbor=False):
    box = (40, 350, 220, 360)
    note = extract.Line([extract.Span('9 oso2. Workbench annotations.', 9, 'ocr', 0, box, uncertain=True)], box)
    bodybox = (40, 348, 270, 349.5) if neighbor else (40, 290, 270, 306)
    body = extract.Line([extract.Span('The original body continues without a terminal boundary', 12, 'ocr', 0, bodybox)], bodybox)
    blocks = [extract.Block(0, bodybox, [body]), extract.Block(1, box, [note])]
    if mixed:
        blocks = [extract.Block(0, (40, 290, 270, 360), [body, note])]
    raw = extract.RawPage(0, 600, 400, blocks, images=[extract.Image((0, 0, 600, 400), 1)])
    notes = [] if unproved else [skeleton.Region('note', lines=[note], number=9, bbox=box)]
    kept = [(blocks[0], [body])]
    if unproved:
        kept.append((blocks[-1], [note]))
    sk = skeleton.PageSkeleton(0, 600, 400, is_scan=True)
    candidates = [skeleton.Region('figure', bbox=(30, 280, 280, 370))] if carrier else []
    return raw, kept, notes, sk, candidates


def delivered(raw, kept, notes, sk, candidates, neighbor_kind='body', neighbor=None):
    before = asdict(raw)
    kept, notes = skeleton._preserve_uncertain_ocr_regions(raw, kept, notes, sk, None, candidates)
    sk.regions.extend(skeleton.Region(neighbor_kind if neighbor in group else 'body',
        lines=group, bbox=block.bbox) for block, group in kept)
    sk.regions.extend(notes + candidates)
    sk.regions.sort(key=skeleton._region_order)
    book = assemble.assemble([sk], skeleton.BookStyle(body_size=12), [raw])
    assert book.conservation.ok, book.conservation.to_dict()
    assert asdict(raw) == before, 'Source literal, geometry and span provenance must remain unchanged'
    chapter = SimpleNamespace(blocks=build_epub.split_blocks(build_epub.page_fragment(book, 0)))
    reading_flow.finish([chapter], book)
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + ''.join(chapter.blocks) + '</root>')
    return book, root, sk


def assert_source_note_lane(book, root, expected=1):
    lane = root.find('section[@class="source-notes"]')
    assert lane is not None
    footnotes = lane.findall('.//aside[@class="footnote"]')
    assert len(footnotes) == expected
    assert len(lane.findall('.//a[@class="source-glyph"]')) == expected
    assert all(note.source_layout and note.uses_source_image for note in book.notes)
    assert len(book.notes) == expected
    assert not root.findall('aside[@class="footnote"]')
    assert not root.findall('aside[@class="source-note-context"]')
    assert 'oso2' not in ''.join(root.itertext()), 'Damaged recognized text must not become a visible transcription'
    assert root.find('p') is not None
    assert list(root).index(root.find('p')) < list(root).index(lane)


@pytest.mark.parametrize('boundary', [None, 'mixed', 'carrier', 'unproved', 'neighbor'])
def test_opaque_note_context_preserves_existing_note_role_through_mixed_source_blocks(boundary):
    raw, kept, notes, sk, candidates = prepared(boundary == 'mixed', boundary == 'carrier',
        boundary == 'unproved', boundary == 'neighbor')
    book, root, _ = delivered(raw, kept, notes, sk, candidates)
    if boundary == 'unproved':
        assert not book.notes
        assert not root.findall('.//aside[@class="footnote"]')
        assert any(art['text'].startswith('9 oso2.') for art in book.artwork)
    else:
        assert_source_note_lane(book, root)
        assert book.notes[0].bbox == (40, 350, 220, 360)
        assert native_text.note_descriptor(book.notes[0])['bbox'] == [40, 350, 220, 360]
    expected_note_images = sum(note.uses_source_image for note in book.notes)
    assert len(root.findall('.//img')) == len(book.figures) + expected_note_images


def test_source_layout_note_crop_never_expands_into_detached_neighboring_body_line():
    raw, kept, notes, sk, candidates = prepared(neighbor=True)
    body = raw.blocks[0].lines[0]
    body.bbox = (40, 345, 270, 348.5)
    body.spans[0].bbox = body.bbox
    raw.blocks[0].bbox = body.bbox
    book, root, _ = delivered(raw, kept, notes, sk, candidates)
    assert_source_note_lane(book, root)
    crop = native_text.note_descriptor(book.notes[0])['bbox']
    assert crop == [40, 350, 220, 360]
    assert body.bbox[3] < crop[1]
    assert not assemble.source_note_context_figures(book, 0)


@pytest.mark.parametrize('neighbor_kind', ['note', 'body', 'caption', 'artwork'])
def test_proven_note_keeps_its_exact_crop_and_does_not_steal_neighbor_source_roles(neighbor_kind):
    raw, kept, notes, sk, candidates = prepared()
    box = (40, 359.75, 220, 369.25)
    neighbor = extract.Line([extract.Span('10 Another source line.', 9, 'ocr', 0, box)], box)
    raw.blocks.append(extract.Block(2, box, [neighbor]))
    if neighbor_kind == 'note':
        notes.append(skeleton.Region('note', lines=[neighbor], number=10, bbox=box,
            reason='pixel_note_territory'))
    else:
        kept.append((raw.blocks[-1], [neighbor]))
    book, root, result = delivered(raw, kept, notes, sk, candidates, neighbor_kind, neighbor)
    assert_source_note_lane(book, root, 2 if neighbor_kind == 'note' else 1)
    first = book.notes[0]
    assert native_text.note_descriptor(first)['bbox'] == [40, 350, 220, 360]
    owners = [region for region in result.regions if any(neighbor is line for line in region.lines)]
    assert len(owners) == 1 and owners[0].kind == neighbor_kind
    if neighbor_kind != 'note':
        assert all(note.bbox != box for note in book.notes)


@pytest.mark.parametrize('uncertainty', ['uncertain', 'transcription_uncertain', 'encoding_unresolved', 'line_transcription_uncertain'])
def test_every_source_reading_uncertainty_keeps_proved_ocr_note_pixels_in_final_lane(uncertainty):
    raw, kept, notes, sk, candidates = prepared()
    row = notes[0].lines[0]
    row.spans[0].uncertain = False
    if uncertainty == 'line_transcription_uncertain':
        row.transcription_uncertain = True
    else:
        setattr(row.spans[0], uncertainty, True)
    book, root, _ = delivered(raw, kept, notes, sk, candidates)
    assert_source_note_lane(book, root)
    assert book.notes[0].num is None, 'Merged uncertain OCR opening is not independent numeric source proof'


@pytest.mark.parametrize('boundary', ['merged', 'uncertain', 'transcription_uncertain',
    'encoding_unresolved', 'flat', 'oversized', 'no_gap', 'mismatched_number', 'nonopening'])
def test_uncertain_note_territory_does_not_admit_an_unproved_numeric_identity(boundary):
    raw, kept, notes, sk, candidates = prepared()
    row = notes[0].lines[0]
    if boundary != 'merged':
        marker = extract.Span('9 ', 4, 'ocr', 0, (40, 345, 44, 349), origin_y=349)
        words = extract.Span('oso2. Workbench annotations.', 9, 'ocr', 0,
            (50, 350, 220, 360), origin_y=360, uncertain=True)
        row.spans = [marker, words]
        if boundary in ('uncertain', 'transcription_uncertain', 'encoding_unresolved'):
            setattr(marker, boundary, True)
        elif boundary == 'flat':
            marker.bbox = (40, 356, 44, 360)
            marker.origin_y = 360
        elif boundary == 'oversized':
            marker.size = 9
        elif boundary == 'no_gap':
            marker.bbox = (47, 345, 50, 349)
        elif boundary == 'mismatched_number':
            notes[0].number = 8
        elif boundary == 'nonopening':
            row.spans.insert(0, extract.Span('Some ', 9, 'ocr', 0, (24, 350, 35, 360), origin_y=360))
        row.bbox = (min(s.bbox[0] for s in row.spans), 345, 220, 360)
        raw.blocks[1].bbox = row.bbox
        notes[0].bbox = row.bbox
    book, root, result = delivered(raw, kept, notes, sk, candidates)
    assert_source_note_lane(book, root)
    assert notes[0].number is None, 'Note location does not prove its OCR numeric label'
    assert book.notes[0].num is None
    assert not root.findall('.//a[@class="noteref"]')
    assert not any(node.get('id', '').startswith('fn_') for node in root.iter())


@pytest.mark.parametrize('number', [9, 137])
def test_separately_raised_reliable_source_marker_can_keep_existing_numeric_identity(number):
    raw, kept, notes, sk, candidates = prepared()
    row = notes[0].lines[0]
    marker = extract.Span(str(number) + ' ', 4, 'ocr', 0,
        (40, 345, 47, 349), origin_y=349)
    words = extract.Span('oso2. Workbench annotations.', 9, 'ocr', 0,
        (52, 350, 220, 360), origin_y=360, uncertain=True)
    row.spans = [marker, words]
    row.bbox = (40, 345, 220, 360)
    raw.blocks[1].bbox = row.bbox
    notes[0].bbox = row.bbox
    notes[0].number = number
    book, root, _ = delivered(raw, kept, notes, sk, candidates)
    assert_source_note_lane(book, root)
    assert book.notes[0].num == number
    assert root.find('.//aside[@id="fn_%s"]' % number) is not None


def test_native_note_provenance_does_not_enter_scan_layout_promotion():
    raw, kept, notes, sk, candidates = prepared()
    for block in raw.blocks:
        for row in block.lines:
            for span in row.spans:
                span.font = 'Times'
    raw.images = []
    sk.is_scan = False
    book, _, _ = delivered(raw, kept, notes, sk, candidates)
    assert len(book.notes) == 1
    assert not book.notes[0].source_layout
    assert book.notes[0].num == 9
