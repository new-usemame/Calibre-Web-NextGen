"""Synthetic mathematical ink must not become alphabetic OCR guesses."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark=pytest.mark.unit


def test_multiword_symbol_edit_cannot_split_into_confident_letter_deletions():
    before='Please compare the symbols T, D. Q, c. beside this clear example.'
    model='Please compare the symbols ∂ ∑ ∞ ∫ beside this clear example.'
    source=[dict(text=t,bbox=[i*25,20,i*25+22,32],confidence=99)
            for i,t in enumerate(before.split())]
    witness=pa.CropWitness(source)
    witness.model_reading=True
    result=pa.adjudicate(model.split(),source,witness)
    assert result['tokens'][:4]==before.split()[:4]
    assert result['tokens'][-4:]==before.split()[-4:]
    for i in range(4,8):
        note=result['word_notes'].get(i,{})
        assert note.get('source_image_only'), 'Alphabetic recognizer agreement does not identify mathematical ink'
        assert note['status']=='UNRESOLVED'
        assert note['source_bbox']==source[i]['bbox']
    assert set(result['marked'])==set(range(4,8))


def test_literal_latin_letters_with_matching_readings_stay_live():
    text='Please compare the labels T D Q c beside this clear example.'
    source=[dict(text=t,bbox=[i*25,20,i*25+22,32],confidence=99)
            for i,t in enumerate(text.split())]
    witness=pa.CropWitness(source);witness.model_reading=True
    result=pa.adjudicate(text.split(),source,witness)
    assert result['tokens']==text.split()
    assert not result['marked']


@pytest.mark.parametrize('model, optical', [('(2)', '(4)'), ('[7]', '1'), ('2', '(4)')])
def test_conflicting_enclosed_digit_guesses_do_not_identify_a_printed_mark(model, optical):
    source=[dict(text=t,bbox=[i*30,20,i*30+25,34],confidence=99)
            for i,t in enumerate(('Compare label '+optical+' here.').split())]
    witness=pa.CropWitness(source)
    witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word)],[dict(word)]]
    result=pa.adjudicate(('Compare label '+model+' here.').split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED' and note.get('source_image_only')
    assert note['source_bbox']==source[2]['bbox']
    assert note['method']=='unproved_mathematical_symbol_identity'
    assert result['tokens'][0:2]==['Compare','label']
    assert result['tokens'][-1]=='here.'


@pytest.mark.parametrize('native',[False,True])
def test_agreeing_enclosed_digit_or_visible_native_number_stays_live(native):
    text='Compare label (4) here.'
    source=[dict(text=t,bbox=[i*30,20,i*30+25,34],confidence=99)
            for i,t in enumerate(text.split())]
    witness=pa.CropWitness(source)
    witness.model_reading=True
    model='Compare label (2) here.' if native else text
    result=pa.adjudicate(model.split(),source,witness,source_print=native)
    assert result['tokens']==text.split()
    assert not any(n.get('source_image_only')for n in result['word_notes'].values())


def test_compiled_drawn_mark_is_print_instead_of_either_numeric_guess(tmp_path,monkeypatch):
    import re
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    source=[dict(text=t,bbox=[x,20,x+35,35],confidence=99)
            for t,x in zip(['Check','label','(4)','now.'],[20,70,110,150])]
    witness=pa.CropWitness(source)
    witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word)],[dict(word)]]
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open()as doc:
        page=doc.new_page(width=230,height=80)
        for text,x in [('Check',20),('label',70),('(',110),(')',132),('now.',150)]:
            page.insert_text((x,32),text,fontsize=11)
        # A synthetic drawn summation-like mark, containing no printed digit.
        for a,b in [((126,22),(117,22)),((117,22),(123,27)),
                    ((123,27),(117,32)),((117,32),(126,32))]:
            page.draw_line(a,b,width=.8)
        compiled,record=vp.compile_page(answer(block('Check label (2) now.',bbox=(0,0,1000,1000))),[],
            SourceDisplay(doc,0),0,Package(),{},pixel_words=source,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    unit=record['blocks'][0]
    assert 2 in unit['source_image_words']
    live=re.sub(r'<[^>]+>','',''.join(compiled['body']))
    assert '(4)'not in live and '(2)'not in live
    assert 'source-word-image'in ''.join(compiled['body'])
