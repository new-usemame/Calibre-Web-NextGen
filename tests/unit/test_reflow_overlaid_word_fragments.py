"""Synthetic overlapping text-layer aliases are one printed occurrence."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('separate',[False,True])
def test_complete_pixel_word_cannot_publish_an_overlaid_extra_letter(separate):
    whole=dict(text='garden',bbox=[40,20,82,32],confidence=99)
    fragment=dict(text='de',bbox=[60,20,70,32]if not separate else[90,20,100,32],confidence=99)
    source=[dict(text='The',bbox=[10,20,30,32],confidence=99),whole,fragment,
            dict(text='stays',bbox=[110,20,140,32],confidence=99)]
    witness=pa.CropWitness([dict(w)for w in source if w is not fragment])
    result=pa.adjudicate(['The','garden','stays'],source,witness)
    assert result['tokens']==(['The','garden','de','stays']if separate else ['The','garden','stays'])
    if not separate:assert not result['marked'],'The same original ink is not a new source word or uncertainty'


@pytest.mark.parametrize('fragment_text', ['arde', 'arden'])
@pytest.mark.parametrize('evidence', ['complete', 'separate', 'unconfirmed'])
def test_complete_pixel_word_owns_a_longer_overlaid_fragment(fragment_text, evidence):
    whole = dict(text='garden', bbox=[40, 20, 82, 32], confidence=99)
    fragment = dict(text=fragment_text,
                    bbox=[50, 22, 72, 30] if evidence != 'separate' else [90, 20, 112, 32],
                    confidence=99)
    source = [dict(text='The', bbox=[10, 20, 30, 32], confidence=99), whole, fragment,
              dict(text='stays', bbox=[120, 20, 150, 32], confidence=99)]
    witness = pa.CropWitness([dict(w, confidence=60 if evidence == 'unconfirmed' else 99)
                              for w in source if w is not fragment])
    result = pa.adjudicate(['The', 'garden', 'stays'], source, witness)
    assert result['tokens'] == (['The', 'garden', 'stays'] if evidence == 'complete'
                                else ['The', 'garden', fragment_text, 'stays'])
    if evidence == 'complete':
        assert not result['marked'], 'A proved whole occurrence cannot acquire its own suffix as a new word'


@pytest.mark.parametrize('layout', ['alias', 'separate', 'weak', 'ambiguous', 'native'])
def test_overlapping_source_copies_need_one_complete_pixel_occurrence(layout):
    first = dict(text='garden', bbox=[40, 20, 82, 32], confidence=99)
    copy = dict(text='garden', bbox=[43, 22, 79, 31], confidence=99)
    if layout == 'separate':
        copy['bbox'] = [90, 20, 132, 32]
    source = [dict(text='The', bbox=[10, 20, 30, 32], confidence=99), first, copy,
              dict(text='stays', bbox=[145, 20, 175, 32], confidence=99)]
    witness = pa.CropWitness([dict(w, confidence=60 if layout == 'weak' else 99)
                              for w in source if w is not copy])
    if layout in ('separate', 'ambiguous'):
        witness.insert(2, dict(copy))
    native = layout == 'native'
    result = pa.adjudicate(['The', 'garden', 'stays'], source, witness, source_print=native)
    assert result['tokens'] == (['The', 'garden', 'stays'] if layout == 'alias'
                                else ['The', 'garden', 'garden', 'stays'])
    if layout == 'alias':
        assert not result['marked'], 'An overlaid copy is neither omitted text nor a doubtful neighbor'


def test_complete_source_owner_contains_a_shifted_tail_copy():
    source = [dict(text='The', bbox=[40, 20, 54, 31]),
              dict(text='The', bbox=[49, 22, 54, 32]),
              dict(text='garden', bbox=[60, 20, 100, 32])]
    witness = pa.CropWitness([dict(text='The', bbox=[39, 22, 52, 35], confidence=99),
                              dict(source[2], confidence=99)])
    result = pa.adjudicate(['The', 'garden'], source, witness)
    assert result['tokens'] == ['The', 'garden']
    assert not result['marked']


def test_page_agreement_sweep_uses_the_canonical_source_inventory(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer, block

    with pymupdf.open() as drawn, pymupdf.open() as scanned:
        page = drawn.new_page(width=300, height=180)
        page.insert_text((30, 60), 'The garden stays green.', fontsize=12)
        optical = [dict(text=w[4], bbox=list(w[:4]), confidence=99)
                   for w in page.get_text('words')]
        page = scanned.new_page(width=300, height=180)
        page.insert_image(page.rect, pixmap=drawn[0].get_pixmap())
        source = [optical[0], optical[1], dict(optical[1]), *optical[2:]]
        source[2]['bbox'] = [v + .2 for v in source[2]['bbox']]
        monkeypatch.setattr(pa, 'read_crop', lambda *args, **kwargs: pa.CropWitness(optical))
        audited = []
        original_audit = pa.audit_agreement
        def audit(tokens, words, *args, **kwargs):
            audited.append([w['text'] for w in words])
            return original_audit(tokens, words, *args, **kwargs)
        monkeypatch.setattr(pa, 'audit_agreement', audit)
        class Package:
            def disclosure_image(self, *args):
                pass
        _, record = vp.compile_page(answer(block('The garden stays green.')), source,
            SourceDisplay(scanned, 0), 0, Package(), {}, pixel_words=optical,
            regions={'b1': [[25, 40, 250, 65]]}, body_owners={},
            note_context={'cache': tmp_path/'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    assert audited == [['The', 'garden', 'stays', 'green.']]
    assert record['blocks'][0]['published_tokens'] == ['The', 'garden', 'stays', 'green.']
