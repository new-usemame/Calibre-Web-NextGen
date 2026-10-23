"""Synthetic reverse-contrast ink disproves a plausible local OCR word."""
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('reverse',[False,True])
def test_reverse_local_counterread_is_ink_without_an_unproved_live_replacement(tmp_path,monkeypatch,reverse):
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=120)
        if reverse:page.draw_rect(page.rect,fill=(0,0,0))
        page.insert_text((20,55),'The from stays clear.'if reverse else 'The form stays clear.',
            fontsize=14,color=(1,1,1)if reverse else(0,0,0))
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        words[1]['text']='form'
        witness=pa.CropWitness(words)
        witness.word_readings=lambda word:[[dict(text='from',bbox=word['bbox'],confidence=96)]]if word['text']=='form'else []
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        class Package:
            def image(self,*args):pass
        published,record=vp.compile_page(answer(block('The form stays clear.',bbox=(0,0,1000,1000))),[],
            SourceDisplay(doc,0),0,Package(),{},pixel_words=words,local_ocr=True,
            local_ocr_units=[dict(id='b1',words=words,source_word_indices=list(range(len(words))))],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    observed=record['blocks'][0]
    assert observed['source_image_words']==[]
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    assert_flagged_print(''.join(published['body']), 'The form stays clear.'.split())
    if reverse:assert any(d['status']=='UNRESOLVED'for d in observed['adjudication'])
    assert observed['published_tokens'][1]=='form','one counterread cannot certify a replacement'
