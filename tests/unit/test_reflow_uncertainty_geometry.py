"""Synthetic repeated tokens bind confidence only to a unique geometric owner."""
import json
from xml.etree import ElementTree as ET

import pytest

from cps.services.reflow import annotate, assemble, enriched_source

pytestmark = pytest.mark.unit


def paragraph(text, box):
    return assemble.Element('p', runs=[['t', text]], pno=0, bbox=box,
                            line_boxes=[box])


def prepared(elements, box, *, angle=0, frame=(0, 0, 200, 300)):
    book = assemble.Book(elements=elements, pages={0: elements})
    record = {'token': 'Mark', 'candidates': ['Mask'], 'source_bbox': box}
    provenance = {'layer': 'ocr', 'orientation': angle, 'page_rect': frame,
                  'uncertain_words': 1, 'uncertain': [record]}
    return enriched_source.prepare_source_page(book, 0, provenance, [record])


def marks(source):
    tree = ET.fromstring('<root>' + source.html + '</root>')
    return [[node.text for node in p.iter('span')
             if node.get('class') == annotate.CLASS] for p in tree.findall('p')]


def test_repeated_word_marks_only_its_own_second_paragraph():
    source = prepared([paragraph('Mark is clear.', (10, 10, 160, 30)),
                       paragraph('Mark is damaged.', (10, 80, 160, 100))],
                      [10, 80, 40, 100])
    assert marks(source) == [[], ['Mark']]
    report = source.report()
    assert report['marked'] == 1 and report['placed_record_indices'] == [0]
    assert report['unplaced_record_indices'] == []
    assert report['uncertain'][0]['source_bbox'] == [10, 80, 40, 100]


@pytest.mark.parametrize('kind', ['same-block', 'overlap', 'outside', 'invalid'])
def test_ambiguous_or_unowned_geometry_stays_disclosed_unplaced(kind):
    elements = [paragraph('Mark is clear.', (10, 10, 160, 30)),
                paragraph('Mark is damaged.', (10, 80, 160, 100))]
    box = [10, 80, 40, 100]
    if kind == 'same-block':
        elements = [paragraph('Mark is clear. Mark is damaged.', (10, 10, 160, 100))]
    elif kind == 'overlap':
        elements[0].bbox = elements[1].bbox
        elements[0].line_boxes = elements[1].line_boxes
    elif kind == 'outside':
        box = [10, 180, 40, 200]
    else:
        box = [10, 80, float('nan'), 100]
    source = prepared(elements, box)
    assert not any(marks(source))
    report = source.report()
    assert report['marked'] == 0 and report['placed_record_indices'] == []
    assert report['unplaced_record_indices'] == [0]
    assert report['uncertain'][0]['token'] == 'Mark'


def test_geometry_without_source_context_cannot_guess_the_first_match():
    html = '<p>Mark is clear. Mark is damaged.</p>'
    record = {'token': 'Mark', 'candidates': [], 'source_bbox': [10, 80, 40, 100]}
    out, unresolved, marked, positions = annotate.annotate_page(
        html, [record], with_placements=True)
    assert out == html and unresolved == [record]
    assert marked == 0 and positions == []


def test_rotated_original_geometry_selects_the_correct_reading_block():
    # A 90-degree turn maps the second source box to reading x=200..220, y=10..40.
    source = prepared([paragraph('Mark is clear.', (270, 10, 290, 160)),
                       paragraph('Mark is damaged.', (200, 10, 220, 160))],
                      [10, 80, 40, 100], angle=90)
    assert marks(source) == [[], ['Mark']]
    assert source.report()['placed_record_indices'] == [0]


def test_an_existing_mark_cannot_make_a_repeated_token_look_unique():
    html = '<p><span class="reflow-uncertain">Mark</span> is clear. Mark is damaged.</p>'
    record = {'token': 'Mark', 'source_bbox': [10, 10, 40, 30]}
    out, placed = annotate.mark_uncertain(html, [record], source_ranges={0: (0, len(html))})
    assert out == html and placed == []


def test_unplaced_geometry_is_visible_in_the_original_page_document():
    from cps.services.reflow import build_epub
    source = prepared([paragraph('Mark is clear. Mark is damaged.', (10, 10, 160, 100))],
                      [10, 80, 40, 100])
    document = build_epub._original_document(
        {'page': 0, 'full': 'images/synthetic.png', 'details': [],
         'source_uncertainty': source.report()}, 'chapter.xhtml', 'en')
    tree = ET.fromstring(document)
    listed = [''.join(node.itertext()) for node in tree.iter('{http://www.w3.org/1999/xhtml}li')]
    assert listed == ['Mark (not highlighted)']
    assert source.report()['unplaced_record_indices'] == [0]


@pytest.mark.parametrize('mode', ['owned', 'duplicate', 'outside'])
def test_model_adoption_keeps_its_own_geometry_occurrence(mode):
    from cps.services.reflow import pipeline, source
    elements = [paragraph('Mark is clear.', (10, 10, 160, 30)),
                paragraph('Mark is damaged.', (10, 80, 160, 100))]
    if mode == 'duplicate':
        elements[0].runs = [['t', 'Mark is damaged.']]
    book = assemble.Book(elements=elements, pages={0: elements})
    record = {'token': 'Mark', 'score': 22,
              'source_bbox': [10, 180 if mode == 'outside' else 80, 40,
                              200 if mode == 'outside' else 100]}
    recovery = source.Recovery()
    recovery.provenance[0] = source.PageRecovery(
        pno=0, layer='ocr', words=6, uncertain_words=1, uncertain=[record])
    result = pipeline.ReflowResult(book=book, recovery=recovery)
    outcome = pipeline.PageOutcome(pno=0)
    html = '<p>Mark is clear.</p><p>Mark is damaged.</p>'
    if mode == 'duplicate':
        html = '<p>Mark is damaged.</p><p>Mark is damaged.</p>'
    pipeline._adopt(result, book, 0, outcome, html, [], {}, False)
    assert outcome.gate == 'PASS', outcome.gate_reasons
    tree = ET.fromstring('<root>' + result.page_html[0] + '</root>')
    marked = [[node.text for node in p.iter('span')
               if node.get('class') == annotate.CLASS] for p in tree.findall('p')]
    assert marked == ([[], ['Mark']] if mode == 'owned' else [[], []])
    assert outcome.marked == (1 if mode == 'owned' else 0)
    assert outcome.uncertain[0]['source_bbox'] == record['source_bbox']


def test_generated_disclosure_words_do_not_compete_with_the_source_occurrence():
    html = ('<p>source <span class="reflow-uncertain">7'
            '<span class="source-reading-annotation">Uncertain source mark.</span>'
            '</span></p>')
    record = {'token': 'source', 'source_bbox': [10, 10, 40, 30]}
    out, placed = annotate.mark_uncertain(html, [record], source_ranges={0: (0, len(html))})
    tree = ET.fromstring(out)
    assert len(placed) == 1
    assert tree[0].text == 'source'
    assert ''.join(tree[1].itertext()) == '7Uncertain source mark.'


def test_model_source_block_mapping_uses_transcript_instead_of_output_order():
    elements = [paragraph('Mark is clear.', (10, 10, 160, 30)),
                paragraph('Mark is damaged.', (10, 80, 160, 100))]
    book = assemble.Book(elements=elements, pages={0: elements})
    html = '<p>Mark is damaged.</p><p>Mark is clear.</p>'
    record = {'token': 'Mark', 'source_bbox': [10, 80, 40, 100]}
    ranges = enriched_source.model_uncertainty_ranges(book, 0, {}, [record], html)
    out, placed = annotate.mark_uncertain(html, [record], source_ranges=ranges)
    tree = ET.fromstring('<root>' + out + '</root>')
    assert len(placed) == 1 and tree[0][0].text == 'Mark'
    assert list(tree[1]) == []


def test_model_alternatives_cannot_discard_recovered_occurrence_geometry():
    from cps.services.reflow import pipeline, source
    elements = [paragraph('Mark is clear.', (10, 10, 160, 30)),
                paragraph('Mark is damaged.', (10, 80, 160, 100))]
    book = assemble.Book(elements=elements, pages={0: elements})
    record = {'token': 'Mark', 'score': 22, 'source_bbox': [10, 80, 40, 100]}
    recovery = source.Recovery()
    recovery.provenance[0] = source.PageRecovery(
        pno=0, layer='ocr', words=6, uncertain_words=1, uncertain=[record])
    result = pipeline.ReflowResult(book=book, recovery=recovery)
    outcome = pipeline.PageOutcome(pno=0)
    pipeline._adopt(result, book, 0, outcome,
                    '<p>Mark is clear.</p><p>Mark is damaged.</p>',
                    [{'token': 'Mark', 'candidates': ['Mask']}], {}, False)
    assert outcome.gate == 'PASS', outcome.gate_reasons
    tree = ET.fromstring('<root>' + result.page_html[0] + '</root>')
    assert list(tree[0]) == []
    assert tree[1][0].text == 'Mark'
    assert tree[1][0].get('title') == 'likely: Mask'
    assert outcome.uncertain[0]['source_bbox'] == record['source_bbox']
    assert outcome.uncertain[0]['score'] == 22


def test_model_alternatives_preserve_every_distinct_recovered_occurrence():
    from cps.services.reflow import pipeline, source
    elements = [paragraph('Mark is clear.', (10, 10, 160, 30)),
                paragraph('Mark is damaged.', (10, 80, 160, 100)),
                paragraph('Mark is faded.', (10, 140, 160, 160))]
    book = assemble.Book(elements=elements, pages={0: elements})
    records = [{'token': 'Mark', 'score': 22, 'source_bbox': [10, 80, 40, 100]},
               {'token': 'Mark', 'score': 31, 'source_bbox': [10, 140, 40, 160]}]
    recovery = source.Recovery()
    recovery.provenance[0] = source.PageRecovery(
        pno=0, layer='ocr', words=9, uncertain_words=2, uncertain=records)
    result = pipeline.ReflowResult(book=book, recovery=recovery)
    outcome = pipeline.PageOutcome(pno=0)
    pipeline._adopt(result, book, 0, outcome,
                    '<p>Mark is clear.</p><p>Mark is damaged.</p><p>Mark is faded.</p>',
                    [{'token': 'Mark', 'candidates': ['Mask']}], {}, False)
    assert outcome.gate == 'PASS', outcome.gate_reasons
    tree = ET.fromstring('<root>' + result.page_html[0] + '</root>')
    assert list(tree[0]) == []
    assert [tree[i][0].text for i in (1, 2)] == ['Mark', 'Mark']
    assert outcome.marked == 2
    assert [r['source_bbox'] for r in outcome.uncertain] == [r['source_bbox'] for r in records]
    assert [r['score'] for r in outcome.uncertain] == [22, 31]
    assert [r['candidates'] for r in outcome.uncertain] == [['Mask'], ['Mask']]
