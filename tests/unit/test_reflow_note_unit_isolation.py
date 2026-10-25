"""Synthetic text and geometry authored for isolated note-association controls.

The procedural page, note labels and proof receipts are test inputs. Existing
source-display and recovery helpers exercise rendering without OCR or network.
"""
import copy
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from cps.services.reflow import unit_recovery as ur, vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay

pytestmark = pytest.mark.unit

BODY = 'The clear sample <n:note> remains readable beside the window.'
NOTE = 'A short printed note remains readable.'


def block(text, ident, role, bbox, marker=''):
    return dict(id=ident, text=text, role=role, marker=marker, bbox=bbox,
                low_confidence=[], continues_previous=False, continues_next=False)


def rejected_note(tmp_path):
    answer = dict(blocks=[block(BODY, 'body', 'body', [50, 80, 950, 220]),
                          block(NOTE, 'note', 'footnote', [50, 750, 950, 850], '9')])
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    controller.answers[0] = copy.deepcopy(answer)
    controller.admit_answer(0, answer)
    controller.degraded[(0, 'note')] = dict(page=0, block='note', stage='note_ownership',
        reason='The printed label does not prove this note association.',
        status='DEGRADED_SOURCE_CONTEXT')
    return controller, answer


def test_rejected_note_does_not_reject_its_supported_body(tmp_path):
    controller, answer = rejected_note(tmp_path)
    selected = controller.filter_answer(0, answer)
    vp.vision_reading.validate(selected)
    assert set(controller.blocked(0)) == {'note'}
    assert next(b for b in selected['blocks'] if b['id'] == 'body') == answer['blocks'][0]
    stub = next(b for b in selected['blocks'] if b['id'] == 'note')
    assert stub['text'] == stub['marker'] == ''
    controller.admit_answer(0, selected)
    assert controller.source_answer(0) == answer
    assert controller.answers[0] == answer


@pytest.mark.parametrize('proved', [True, False])
def test_unproved_note_association_preserves_body_and_links_only_source(tmp_path, monkeypatch, proved):
    controller, answer = rejected_note(tmp_path)
    images = {}
    package = SimpleNamespace(image=lambda name, data: images.setdefault(name, data))
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 60), 'The clear sample', fontsize=9)
        marker_x = 20 + pymupdf.get_text_length('The clear sample', fontsize=9)
        page.insert_text((marker_x+3, 55), '7', fontsize=5)
        page.insert_text((marker_x+11, 60), 'remains readable beside the window.', fontsize=9)
        page.insert_text((20, 320), '7 '+NOTE, fontsize=8)
        words = [dict(text=w[4], bbox=list(w[:4]), confidence=99)
                 for w in page.get_text('words', sort=True)]
        display = SourceDisplay(doc, 0)
        marker_box = [marker_x+3, 49, marker_x+7, 57]
        proof = dict(status='PROVED' if proved else 'UNPROVED', source_bbox=marker_box,
                     reason=None if proved else 'synthetic_marker_instance_ambiguous')
        if proved:
            proof.update(label='7', method='synthetic_independent_original_glyph_proof')
        monkeypatch.setattr(vp, '_placeholder_display_evidence', lambda *args: {3: copy.deepcopy(proof)})
        with ur.activated(controller):
            selected = controller.filter_answer(0, answer)
            selected, _ = vp.bind_geometry(selected, words, display)
            scan = dict(answer=selected, optical=words, display=display, regions={})
            owners, catalogue, labels = vp._note_ownership({0: scan})
            assert not owners and not catalogue and not labels
            context = dict(owners=owners, catalogue=catalogue, labels=labels,
                           refs={}, cache=tmp_path/'marker-cache')
            compiled, report = vp.compile_page(selected, words, display, 0, package, {},
                                                pixel_words=words, note_context=context)
            vp._insert_source_units(compiled, report, controller.source_answer(0),
                                    controller.blocked(0), display, package, {})
            vp._finish_notes([compiled], owners, catalogue, context['refs'])
    body = ET.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body'])+'</div>')
    span = body.find('.//span[@data-source-block="body"]')
    assert span is not None
    controls = span.findall('.//a')
    assert controls and all(c.get('class') == 'source-disclosure-ref' for c in controls)
    assert len(report['note_marker_disclosures']) == 1
    disclosure = report['note_marker_disclosures'][0]
    marker_controls = [c for c in controls if c.get('id') == disclosure['source_reference_origin']]
    assert len(marker_controls) == 1
    assert ''.join(marker_controls[0].itertext()) == ('7' if proved else '†')
    target = marker_controls[0].get('href')[1:]
    assert target != 'v4-note-0-note'
    assert any('id="'+target+'"' in aside for aside in compiled['asides'])
    # The independent alignment may also disclose the optical marker token.
    # Removing disclosure controls must leave every supported body word once.
    plain = (span.text or '') + ''.join(c.tail or '' for c in controls)
    assert ' '.join(plain.split()) == BODY.replace(' <n:note>', '')
    assert context['refs'] == {}
    assert len(report['note_marker_disclosures']) == 1
    disclosure = report['note_marker_disclosures'][0]
    assert disclosure['association_status'] == 'UNPROVED'
    assert disclosure['unit_bbox'] == marker_box
    assert {u['block'] for u in report['degraded_units']} == {'note'}
    assert images and all(data for data in images.values())
    assert sum('id="v4-note-0-note"' in a for a in compiled['asides']) == 1


def test_conversion_keeps_live_body_when_note_reading_is_rejected(tmp_path, monkeypatch):
    from tests.unit import test_round4_defects as ink
    page = ink.InkPage()
    page.text('The clear sample remains readable beside the window.', 'body',
              y=60, model=BODY)
    page.text('7 '+NOTE, 'note', role='footnote', y=320, size=8, marker='7',
              model='qVrTx mNpRs wXyZq aBcDe fGhIj kLmNo pQrSt uVwXy zAbCd eFgHi jKlMn oPqRs')
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision'
    assert {u['block'] for u in record['degraded_units']} == {'note'}
    assert BODY.replace(' <n:note>', '') in ink.body_texts(docs)
    assert len(record['note_marker_disclosures']) == 1
    assert record['note_marker_disclosures'][0]['association_status'] == 'UNPROVED'
    refs, _ = ink.refs_and_notes(docs)
    assert refs == []


@pytest.mark.parametrize('invalid_rectangle', [[900, 900, 800, 950], [20, 900, 980, 1100]])
def test_unplaced_note_geometry_keeps_its_supported_body_live(tmp_path, monkeypatch, invalid_rectangle):
    from tests.unit import test_round4_defects as ink
    page = ink.InkPage()
    page.text('The clear sample remains readable beside the window.', 'body',
              y=60, model=BODY)
    note_lines = ['7 '+NOTE, 'Additional ordinary words explain the source context.']
    note = page.text(note_lines, 'note', role='footnote', y=320, size=8, marker='7')
    note['bbox'] = invalid_rectangle
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision'
    assert {u['block'] for u in record['degraded_units']} == {'note'}
    assert ' '.join(ink.body_texts(docs)).count(BODY.replace(' <n:note>', '')) == 1
    assert NOTE not in ' '.join(ink.body_texts(docs))
    assert note_lines[1] not in ' '.join(ink.body_texts(docs))
    assert len(record['note_marker_disclosures']) == 1
    assert record['note_marker_disclosures'][0]['association_status'] == 'UNPROVED'
    refs, _ = ink.refs_and_notes(docs)
    assert refs == []
    retained = [a for root in docs.values() for a in root.findall('.//x:aside', ink.NS)
                if a.get('id') == 'v4-note-0-note']
    assert len(retained) == 1
    assert 'vision-disclosure' in retained[0].get('class', '').split()
    assert retained[0].find('x:img', ink.NS) is not None
    assert not any(e.get('class') == 'source-original' for root in docs.values() for e in root.iter())


def test_unplaced_note_identity_has_context_coverage_without_pixel_ownership(tmp_path):
    controller, answer = rejected_note(tmp_path)
    answer['blocks'][1]['bbox'] = [900, 900, 800, 950]
    controller.answers[0] = copy.deepcopy(answer)
    controller.admitted_answers.clear()
    selected = controller.filter_answer(0, answer)
    vp.vision_reading.validate(selected)
    regions = {}
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 60), BODY.replace(' <n:note>', ''), fontsize=9)
        words = [dict(text=w[4], bbox=list(w[:4]), confidence=99)
                 for w in page.get_text('words', sort=True)]
        display = SourceDisplay(doc, 0)
        with ur.activated(controller):
            bound, repairs = vp.bind_geometry(selected, words, display, regions)
            coverage = vp._omission_coverage(bound, display, regions)
        assert regions['note'] == []
        assert 'note' not in regions.get('_instances', {}).values()
        assert coverage['note'] == [list(display.rect)]
        assert 'note' not in repairs
    assert controller.source_answer(0) == answer
    assert next(b for b in bound['blocks'] if b['id'] == 'note')['text'] == ''


def test_unbound_note_with_later_unit_does_not_reject_the_page(tmp_path, monkeypatch):
    from tests.unit import test_round4_defects as ink
    page = ink.InkPage()
    healthy = 'The clear sample remains readable beside the window.'
    page.text(healthy, 'body', y=60, model=BODY)
    note = page.text('7 '+NOTE, 'note', role='footnote', y=290, size=8, marker='7',
                     model='Unlocated purple fabric covers a distant wooden panel.')
    note['bbox'] = [900, 900, 800, 950]
    later = 'The second printed note retains its separate original words.'
    page.text('8 '+later, 'later', role='footnote', y=340, size=7, marker='8', model=later)
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision'
    assert {u['block'] for u in record['degraded_units']} == {'note'}
    assert any('cannot be bound to printed pixels' in u['reason'] for u in record['degraded_units'])
    assert ' '.join(ink.body_texts(docs)).count(healthy) == 1
    assert not any(e.get('class') == 'source-original' for root in docs.values() for e in root.iter())
