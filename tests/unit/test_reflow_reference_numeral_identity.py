"""Synthetic Roman reference ink must not be certified as a decimal digit."""
import pytest
from cps.services.reflow import pixel_adjudication as pa
from tests.unit.test_reflow_pixel_adjudication import words

pytestmark=pytest.mark.unit


def test_repeated_ocr_digit_cannot_certify_replacing_a_roman_reference():
    source=words('Read section 1.7 beside the sample.')
    witness=pa.CropWitness(source);witness.word_readings=lambda w:[[dict(w)]]*2
    result=pa.adjudicate('Read section I.7 beside the sample.'.split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert note['method']=='unproved_reference_numeral_identity'


@pytest.mark.parametrize('context',['See','scale'])
def test_unsettled_local_reference_is_print_but_ordinary_decimal_is_live(context):
    source=words(context+' 1.7 units')
    result=dict(tokens=[w['text']for w in source],word_notes={},marked=set())
    pa.audit_local_numeric_suffixes(result,{i:w['bbox']for i,w in enumerate(source)},pa.CropWitness(source))
    assert bool(result['word_notes'].get(1,{}).get('source_image_only'))is(context=='See')
    assert result['tokens']==[w['text']for w in source],'The source image preserves the numeral; no replacement I is guessed'


def test_optically_recovered_clause_keeps_unproved_reference_as_its_own_print(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    text='Read the next section. See 1.7.'
    source=words(text)
    witness=pa.CropWitness(source)
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open()as doc:
        doc.new_page(width=400,height=150).insert_text((30,44),'Read the next section. See I.7.')
        page,record=vp.compile_page(answer(block(text,bbox=(0,0,1000,1000))),[],
            SourceDisplay(doc,0),0,Package(),{},pixel_words=source,forced_uncertain=('b1',),
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    unit=record['blocks'][0]
    assert unit['source_image_words']==[]
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    root,flags=assert_flagged_print(''.join(page['body']))
    assert any('1.7.'in ''.join(f.itertext())for f in flags)
    assert unit['flagged_readings']['5']['tier']=='flagged'
    assert any(d['status']=='UNRESOLVED'for d in unit['adjudication'])
    assert unit['published_tokens'][:5]==text.split()[:5]
    assert root.findall('.//div[@class="reflow-unit-print"]//img'), 'The uncertain printed reference keeps visible original ink'
    assert root.findall('.//span[@class="reflow-plausible"]//a[@title="Compare this reading with original print"]')


@pytest.mark.parametrize('proved',[True,False])
def test_local_short_reference_label_needs_complete_independent_identity(proved):
    source=words('Read chapter QN, next.')
    word=source[2]
    witness=pa.CropWitness(source)
    witness.word_scope=lambda value:list(value['bbox'])
    witness.word_readings=lambda value:[[dict(value,confidence=99 if proved else 35)],
        [dict(value,text=value['text']if proved else'1N,',confidence=99 if proved else 20)]]
    result=dict(tokens=[w['text']for w in source],word_notes={},marked=set())
    pa.audit_local_numeric_suffixes(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert result['tokens']==[w['text']for w in source],'Reference identity is never guessed from its context'
    assert bool(result['word_notes'].get(2,{}).get('source_image_only'))is not proved
    assert 0 not in result['marked']and 3 not in result['marked']


@pytest.mark.parametrize('proved', [False, True])
def test_local_roman_shaped_abbreviation_requires_complete_literal_identity(proved):
    source = words('The citation ends VIT.')
    word = source[3]
    witness = pa.CropWitness(source)
    witness.word_readings = lambda value: [
        [dict(value, confidence=99)],
        [dict(value, text=value['text'] if proved else 'VII.1', confidence=99)]]
    result = dict(tokens=[w['text'] for w in source], word_notes={}, marked=set())
    pa.audit_local_numeric_suffixes(result, {i:w['bbox'] for i,w in enumerate(source)}, witness)
    assert bool(result['word_notes'].get(3, {}).get('source_image_only')) is not proved
    assert result['tokens'] == [w['text'] for w in source]
    assert not ({0,1,2} & result['marked'])
