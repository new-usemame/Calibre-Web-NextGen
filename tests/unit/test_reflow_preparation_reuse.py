"""Procedural pages distinguish immutable preparation from failed publication."""
import copy
import pytest
from cps.services.reflow import vision_pipeline as vp, unit_recovery as ur
from tests.unit import test_round4_defects as ink


def test_healthy_page_preparation_runs_once_across_failed_publication(tmp_path, monkeypatch):
    counts = {}
    bind = vp.bind_geometry
    compile_page = vp.compile_page
    failed = False
    def counted(answer, optical, display, *args):
        counts[display.pno] = counts.get(display.pno, 0) + 1
        return bind(answer, optical, display, *args)
    def reject_once(answer, source_words, display, n, *args, **kwargs):
        nonlocal failed
        if n == 1 and not failed:
            failed = True
            raise ur.UnitFailure([dict(page=1, block='body')], 'page_compilation', ValueError('Synthetic publication rejection.'))
        result = compile_page(answer, source_words, display, n, *args, **kwargs)
        if n == 0 and not failed:
            answer['blocks'][0]['text'] = 'Contaminated failed attempt.'
        return result
    monkeypatch.setattr(vp, 'bind_geometry', counted)
    monkeypatch.setattr(vp, 'compile_page', reject_once)
    pages = [ink.InkPage(), ink.InkPage()]
    pages[0].text('The amber sample remains clear.', 'body')
    pages[1].text('The silver sample remains clear.', 'body')
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, pages)
    assert failed and report['degraded_unit_count'] >= 1
    assert counts[0] == 1, 'Healthy source geometry must survive a later publication rejection.'
    assert ink.body_texts(docs)[0] == 'The amber sample remains clear.'
    assert 'Contaminated' not in ' '.join(ink.plain(root) for root in docs.values())


def test_prepared_page_invalidates_on_rereading_and_block_changes(tmp_path):
    class Reader:
        cache = tmp_path/'cache'
    c = ur.Controller(Reader(), [0], tmp_path/'staging')
    c.readings[0] = {'answer': {'blocks': [{'id': 'body', 'text': 'Amber sample.'}]}}
    prepared = {'answer': copy.deepcopy(c.readings[0]['answer']), 'source_words': []}
    c.store_prepared(0, prepared)
    loaded = c.load_prepared(0)
    loaded['answer']['blocks'][0]['text'] = 'Changed mutable attempt.'
    assert c.load_prepared(0) == prepared
    c.readings[0]['answer']['blocks'][0]['text'] = 'Silver sample.'
    assert c.load_prepared(0) is None
    c.store_prepared(0, prepared)
    c.degraded[(0, 'body')] = dict(page=0, block='body', reason='Synthetic rejection.')
    assert c.load_prepared(0) is None


def test_scanned_conversion_never_copies_unused_native_preparation(tmp_path, monkeypatch):
    class UnusedNativePreparation:
        def __deepcopy__(self, memo):
            raise AssertionError('Unused native preparation was copied.')
    convert = vp.convert
    def with_unused_preparation(doc, path, reader):
        return convert(doc, path, reader, native_prepared=UnusedNativePreparation())
    monkeypatch.setattr(vp, 'convert', with_unused_preparation)
    page = ink.InkPage()
    page.text('The clear sample remains readable.', 'body')
    _, _, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    assert ink.body_texts(docs) == ['The clear sample remains readable.']
