"""Synthetic raised suffixes remain printed ink unless pixels identify them."""
import pymupdf
import pytest
from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('settled',[False,True])
def test_local_numeric_guess_cannot_silently_drop_its_raised_suffix(tmp_path,monkeypatch,settled):
    text='Please read 24" beside the open window.'
    source=[dict(text=t,bbox=[20+i*45,30,60+i*45,44],confidence=96)
            for i,t in enumerate(text.split())]
    source[2]['bbox']=[110,28,141,44]
    witness=pa.CropWitness(source)
    if settled:
        witness.word_readings=lambda word:[[
            dict(text='24',confidence=96,bbox=[110,33,130,44]),
            dict(text='th',confidence=96,bbox=[131,28,141,34])]]*2
    else:witness.word_readings=lambda word:[]
    witness.word_scope=lambda word:[110,28,141,44]
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=150)
        p.insert_text((110,44),'24',fontsize=14)
        p.insert_text((127,34),'th',fontsize=8)
        page,record=vp.compile_page(answer(block(text,bbox=(0,0,1000,1000))),[],
            SourceDisplay(doc,0),0,Package(),{},pixel_words=source,local_ocr=True,
            local_ocr_units=[dict(id='b1',words=source,source_word_indices=list(range(len(source))))],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    b=record['blocks'][0]
    if settled:
        assert b['published_tokens'][2]=='24th'
        decision=next(d for d in b['adjudication']if d.get('method')=='two_pixel_readings_raised_numeric_suffix')
        assert decision['status']=='RESOLVED'
        assert 2 not in b['source_image_words']
    else:
        assert 2 in b['source_image_words']
        assert 'source-word-image' in ''.join(page['body'])
    assert 2 in b['marked']
    assert b['published_tokens'][:2]==text.split()[:2]
    assert b['published_tokens'][3:]==text.split()[3:]
