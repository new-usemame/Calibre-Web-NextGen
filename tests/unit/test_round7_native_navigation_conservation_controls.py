"""Independent synthetic original-annotation conservation controls; no real SOURCE."""
import copy
import pymupdf
import pytest
from tests.unit.test_round7_native_navigation_publication_controls import Fixture, Offline
from cps.services.reflow import assemble, extract, unit_recovery, vision_pipeline as vp


def two_references(tmp_path, vector=False):
    f = Fixture(tmp_path, 'vector' if vector else 'outgoing')
    source = f.row(0, 'A neighboring original reference 9 remains here.', 650)
    dest = f.row(1, 'A neighboring destination stays independently owned.', 650)
    f.element(0, [source]); f.element(1, [dest])
    rect = f.doc[0].search_for('reference 9')[0]
    rect.x0 = f.doc[0].search_for('reference')[1].x1 + 1
    f.doc[0].insert_link({'kind': pymupdf.LINK_GOTO, 'from': rect, 'page': 1,
                         'to': pymupdf.Point(dest['bbox'][0], (dest['bbox'][1]+dest['bbox'][3])/2)})
    path = tmp_path / 'original-two-annotations.pdf'
    f.doc.save(path); f.doc.close(); f.doc = pymupdf.open(path)
    f.raw = [extract.read_page(f.doc, n, keep_char_boxes=True) for n in range(2)]
    f.book.elements = sum(f.book.pages.values(), [])
    f.book.source_navigation = []
    assemble._bind_source_navigation(f.book, f.raw)
    f.original_nav = copy.deepcopy(f.book.source_navigation)
    assert len(f.original_nav) == 2 and all(r['status'] == 'resolved' for r in f.original_nav)
    assert {r['label'] for r in f.original_nav} == {'7', '9'}
    return f


@pytest.mark.parametrize('outcome', ['ambiguous_owner', 'destination_unavailable', 'duplicate', 'wrong_owner'])
def test_failed_final_binding_rolls_back_original_units_and_neighbor_actions(tmp_path, monkeypatch, outcome):
    # The ambiguity case also proves discovered vector art cannot disappear on rollback.
    f = two_references(tmp_path, vector=outcome == 'ambiguous_owner')
    before = copy.deepcopy(f.book)
    binder = assemble._bind_source_navigation
    calls = []
    def adversarial(book, raw):
        binder(book, raw)
        target = next(r for r in book.source_navigation if r['label'] == '7')
        neighbor = next(r for r in book.source_navigation if r['label'] == '9')
        calls.append(target['id'])
        if outcome in ('ambiguous_owner', 'destination_unavailable'):
            target['status'] = outcome
        elif outcome == 'duplicate':
            book.source_navigation.append(copy.deepcopy(target))
        else:
            target['source_element'] = neighbor['source_element']
            target['source_offset'] = neighbor['source_offset']
    monkeypatch.setattr(assemble, '_bind_source_navigation', adversarial)
    try:
        repairs, contexts = vp._native_publication_ownership(f.doc, f.book, [0, 1], raw_pages=f.raw)
        assert calls, 'Control must reach the real original binder after optional native reconstruction'
        assert f.book.pages[0] == before.pages[0], 'Failed rebinding must restore original indexed SOURCE units'
        assert f.book.source_navigation == before.source_navigation, 'Every original resolved ID and its unaffected neighbor must survive exactly once at its original proven owner'
        assert f.book.figures == before.figures, 'Rejected optional figure insertion must restore original figure inventory'
        assert any(r.get('kind') == 'native_publication_navigation_retained_original' for r in repairs), 'Rollback must have an explicit report repair reason'
        if outcome == 'ambiguous_owner':
            assert any(c.get('page') == 0 and c.get('seed_bbox') for c in contexts), 'Discovered original vector extent must remain a listed optional SOURCE context when insertion rolls back'
    finally:
        f.close()


def test_unexpected_binder_error_restores_original_ownership_and_keeps_typed_cause(tmp_path, monkeypatch):
    f = two_references(tmp_path)
    before = copy.deepcopy(f.book)
    binder = assemble._bind_source_navigation
    error = ValueError('synthetic original binder failure')
    def broken(book, raw):
        binder(book, raw)
        raise error
    monkeypatch.setattr(assemble, '_bind_source_navigation', broken)
    controller = unit_recovery.Controller(Offline(tmp_path), [0, 1], tmp_path / 'recovery')
    try:
        with pytest.raises(unit_recovery.UnitFailure) as caught:
            with unit_recovery.activated(controller):
                vp._native_publication_ownership(f.doc, f.book, [0, 1], raw_pages=f.raw)
        assert caught.value.cause is error and 0 in caught.value.pages, 'Unexpected binder cause must retain exact typed SOURCE page ownership'
        assert f.book.pages == before.pages and f.book.source_navigation == before.source_navigation and f.book.figures == before.figures, 'Owned failure must restore original validated units, references and figure inventory before entering recovery'
    finally:
        f.close()


def test_healthy_source_binder_keeps_both_proven_references_and_repairs_paragraph(tmp_path):
    f = two_references(tmp_path)
    try:
        repairs, contexts = vp._native_publication_ownership(f.doc, f.book, [0, 1], raw_pages=f.raw)
        assert len(f.book.source_navigation) == 2 and all(r['status'] == 'resolved' for r in f.book.source_navigation)
        assert {r['id'] for r in f.book.source_navigation} == {r['id'] for r in f.original_nav}
        assert any('unfinished ordinary reference 7 through' in e.text for e in f.book.pages[0]), 'Healthy complete SOURCE proof must still permit paragraph reconstruction'
        for r in f.book.source_navigation:
            assert f.book.pages[r['pno']][r['source_element']].text[r['source_offset']:r['source_offset']+r['source_extent']] == r['label']
        assert any(r.get('kind') == 'original_source_native_navigation_rebound' for r in repairs)
    finally:
        f.close()


def test_fatal_binder_resource_error_propagates_unchanged(tmp_path, monkeypatch):
    f = two_references(tmp_path)
    error = MemoryError('synthetic resource exhaustion')
    def broken(book, raw):
        raise error
    monkeypatch.setattr(assemble, '_bind_source_navigation', broken)
    controller = unit_recovery.Controller(Offline(tmp_path), [0, 1], tmp_path / 'recovery')
    try:
        with pytest.raises(MemoryError) as caught:
            with unit_recovery.activated(controller):
                vp._native_publication_ownership(f.doc, f.book, [0, 1], raw_pages=f.raw)
        assert caught.value is error, 'Fatal resource errors must not become ordinary source fallback'
    finally:
        f.close()
