"""Independent public SOURCE recovery after exact owned stage failures.
All printed ink and strings are synthetic; no live Reader or OCR is used.
"""
import importlib.util
from pathlib import Path
import zipfile
from cps.services.reflow import vision_pipeline as vp, vision_reading as vr, build_epub
import pytest

_spec = importlib.util.spec_from_file_location('r7_scope_helpers', Path(vp.__file__).parents[3] / 'tests/unit/test_round4_defects.py')
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

FIRST = 'The amber orchard remains an intact healthy paragraph.'
BAD = 'The disputed cobalt specimen retains every original mark.'
LAST = 'The emerald instrument remains a separate healthy paragraph.'
NOTE = 'A genuine healthy note describes the maple specimen.'
LATER = 'The later indigo page remains live and complete.'


@pytest.mark.parametrize('role', ['body', 'footnote'])
def test_failed_owned_alignment_preserves_healthy_same_page_units(tmp_path, monkeypatch, role):
    page = h.InkPage()
    page.text(FIRST, 'first', y=45)
    page.text(BAD, 'disputed', role, y=130, marker='*' if role == 'footnote' else '')
    page.text(LAST, 'last', y=220)
    page.text(NOTE, 'healthy-note', 'footnote', y=335, size=7, marker='†')
    later = h.InkPage()
    later.text(LATER, 'later', y=60)
    reached = []
    original = vr.align

    def failed_alignment(text, *args, **kwargs):
        result = original(text, *args, **kwargs)
        if text == BAD:
            reached.append(('vision_reading.align', role, text))
            # The guarded block identity must survive this exact stage failure.
            raise ValueError('synthetic owned alignment rejects disputed unit')
        return result

    monkeypatch.setattr(vr, 'align', failed_alignment)
    path, report, docs = h.convert_ink(tmp_path, monkeypatch, [page, later])
    assert reached, 'the selected owned alignment stage must actually fail'
    body = h.body_texts(docs)
    assert body.count(FIRST) == body.count(LAST) == body.count(LATER) == 1
    notes = h.refs_and_notes(docs)[1]
    assert sum(NOTE in ''.join(node.itertext()) for _, node in notes) == 1
    units = report['degraded_units']
    assert {(u['page'], u.get('block')) for u in units} == {(0, 'disputed')}, 'scope must not expand to healthy same-page paragraphs or notes'
    assert all(u.get('stage') and u.get('reason') for u in units)
    with zipfile.ZipFile(path) as archive:
        for unit in units:
            src = unit['source_image']
            assert (src if src.startswith('OEBPS/') else 'OEBPS/' + src) in archive.namelist()
    assert set(map(int, report['page_homes'])) == {0, 1}
    assert report['reread_pages'] == [], 'an offline synthetic Reader cannot issue an invented reread'


def test_failed_seam_after_pop_replays_pristine_units_and_keeps_unrelated_prose(tmp_path, monkeypatch):
    left = h.InkPage()
    left.text(FIRST, 'first', y=45)
    left.text('The patient collector carried the specimen toward', 'tail', y=350, next=True)
    right = h.InkPage()
    right.text('the shaded toolstore beside the river.', 'head', y=45, previous=True)
    right.text(LAST, 'last', y=180)
    later = h.InkPage()
    later.text(LATER, 'later', y=70)
    original = build_epub._merge_layout_paragraphs
    reached = []

    def fail_after_pop(left_fragment, right_fragment, *args, **kwargs):
        # The real join helper has already popped the right source paragraph
        # before entering this merger. Inject inside its original owned guard.
        if 'patient collector' in left_fragment:
            reached.append(right_fragment)
            assert 'shaded toolstore' in right_fragment
            original(left_fragment, right_fragment, *args, **kwargs)
            raise ValueError('synthetic shared seam fails after destructive pop')
        return original(left_fragment, right_fragment, *args, **kwargs)

    monkeypatch.setattr(build_epub, '_merge_layout_paragraphs', fail_after_pop)
    path, report, docs = h.convert_ink(tmp_path, monkeypatch, [left, right, later])
    assert len(reached) == 1
    body = h.body_texts(docs)
    assert body.count(FIRST) == body.count(LAST) == body.count(LATER) == 1
    assert {(u['page'], u.get('block')) for u in report['degraded_units']} == {(0, 'tail'), (1, 'head')}, 'only both known seam fragments may degrade'
    with zipfile.ZipFile(path) as archive:
        for unit in report['degraded_units']:
            assert 'OEBPS/' + unit['source_image'] in archive.namelist()
    assert set(map(int, report['page_homes'])) == {0, 1, 2}
