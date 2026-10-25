"""Synthetic transcription alternatives and occurrence geometry written here."""
import pytest
from cps.services.reflow.pixel_adjudication import adjudicate

pytestmark=pytest.mark.unit


def words(text, confidence=99):
    return [dict(text=t,bbox=[i*20,20,i*20+18,32],confidence=confidence)
            for i,t in enumerate(text.split())]


@pytest.mark.parametrize('conflict', [False, True])
def test_positive_own_optical_word_does_not_need_a_missing_neighbor_read(conflict):
    source=words('The clear sample stays readable.')
    witness=words('The clcar sample stays readable.') if conflict else []
    result=adjudicate('The clear sample stays readable.'.split(),source,witness,uncertain={1})
    if conflict:
        from cps.services.reflow.pixel_adjudication import audit_agreement
        assert 1 in audit_agreement(result['tokens'],source,witness)
    else:
        assert result['tokens']==[w['text']for w in source]
        assert result['marked']==set()


@pytest.mark.parametrize('settled', [False, True])
def test_valid_printed_ordinal_is_not_certified_as_opaque_ocr_letters(settled):
    from cps.services.reflow.pixel_adjudication import CropWitness
    source=words('Read the Bth: sample now.')
    witness=CropWitness(words('Read the Bth: sample now.'))
    witness.word_readings=lambda word:([words('Read the 8th: sample now.')]*2 if settled else [])
    result=adjudicate('Read the 8th: sample now.'.split(),source,witness)
    assert result['tokens'][2]=='8th:'
    assert result['word_notes'][2]['status']==('RESOLVED' if settled else 'UNRESOLVED')
    assert 2 in result['marked']
    assert bool(result['word_notes'][2].get('source_image_only')) is not settled


@pytest.mark.parametrize('printed',['9th','both'])
def test_conflicting_valid_printed_ordinal_still_wins_when_pixels_prove_it(printed):
    source=words('Read the '+printed+' sample now.')
    result=adjudicate('Read the 8th sample now.'.split(),source,source)
    assert result['tokens'][2]==printed
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert 2 in result['marked']


@pytest.mark.parametrize('role',['body','table'])
def test_complete_two_column_territory_keeps_column_order(monkeypatch,role):
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as pipeline
    import pymupdf
    source=[]
    for x,prefix in [(10,'Left'),(150,'Right')]:
        for y in [20,40,60]:
            source.extend([dict(text=prefix,bbox=[x,y,x+35,y+10]),
                           dict(text=str(y),bbox=[x+40,y,x+55,y+10])])
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,250,120))
    block=dict(id='sample',role=role,text='Synthetic columns',bbox=[0,0,1,1])
    result=pipeline._owned_block_words(block,dict(blocks=[block]),source,display,
        regions={'sample':[[0,0,250,120]]},ordered=True)
    expected=source if role=='body'else sorted(source,key=lambda w:(w['bbox'][1],w['bbox'][0]))
    assert [w['text']for w in result]==[w['text']for w in expected]


def test_persistent_word_slots_do_not_mask_actual_column_gutters():
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as pipeline
    import pymupdf
    source=[]
    for x,prefix in [(10,'Left'),(170,'Middle'),(330,'Right')]:
        for y in [20,40,60]:
            source.extend([dict(text=prefix,bbox=[x,y,x+35,y+10]),
                           dict(text=str(y),bbox=[x+60,y,x+95,y+10])])
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,120))
    block=dict(id='sample',role='body',text='Synthetic columns',bbox=[0,0,1,1])
    result=pipeline._owned_block_words(block,dict(blocks=[block]),source,display,
        regions={'sample':[[0,0,500,120]]},ordered=True)
    assert [w['text']for w in result]==[w['text']for w in source]


def test_pixel_read_restores_short_model_deletion():
    source=words('We carried the blue box to the open door today.')
    result=adjudicate('We carried the open door today.'.split(),source,source)
    assert result['tokens']==[w['text'] for w in source]
    assert result['decisions'][0]['status']=='RESOLVED'
    assert result['decisions'][0]['chosen']==['the','blue','box','to']
    assert result['marked']


def test_pixel_read_rejects_inserted_real_word():
    source=words('The clear sample remains readable today.')
    result=adjudicate('The clear sample never remains readable today.'.split(),source,source,
        absence_proof=lambda left,right:True)
    assert result['tokens']==[w['text'] for w in source]
    assert result['decisions'][0]['chosen']==[]
    assert result['decisions'][0]['status']=='RESOLVED'
    assert result['marked']


def test_corroborated_model_correction_stays_marked_word_by_word():
    source=words('The clear sample remalns readable today.')
    result=adjudicate('The clear sample remains readable today.'.split(),source,
                      words('The clear sample remains readable today.'))
    assert result['tokens'][3]=='remains'
    assert result['marked']=={3}
    assert result['word_notes'][3]['source']==['remalns']
    assert result['word_notes'][3]['chosen']==['remains']
    assert result['word_notes'][3]['status']=='RESOLVED'


def test_repeat_without_unique_pixel_context_cannot_settle_edit():
    source=words('The sample remalns readable today.')
    witness=words('The sample remains readable today. The sample remains readable today.')
    result=adjudicate('The sample remains readable today.'.split(),source,witness)
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['marked']=={2}


def test_roman_numeral_rewrite_requires_pixel_agreement():
    source=words('Please read section III before opening the box.')
    result=adjudicate('Please read section 111 before opening the box.'.split(),source,source)
    assert result['tokens'][3]=='III'
    assert result['word_notes'][3]['status']=='RESOLVED'


def test_low_confidence_witness_cannot_certify_a_word():
    source=words('The sample remalns readable today.')
    result=adjudicate('The sample remains readable today.'.split(),source,
                      words('The sample remains readable today.',30))
    assert result['word_notes'][2]['status']=='UNRESOLVED'


def test_real_conversion_restores_model_deleted_words(tmp_path,monkeypatch):
    import shutil
    if not shutil.which('tesseract'):pytest.skip('requires real OCR')
    from tests.unit import test_round4_defects as ink
    page=ink.InkPage()
    printed='We carried the blue box to the open door today.'
    page.text(printed,'body',model='We carried the open door today.')
    _,report,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    assert ink.body_texts(docs)==[printed]
    decisions=report['pages'][0]['blocks'][0]['adjudication']
    assert any(d['status']=='RESOLVED' and d['chosen']!=d['model'] for d in decisions)
    disclosures=[n for root in docs.values() for n in root.iter()
                 if n.get('class')=='footnote vision-disclosure']
    assert len(disclosures)==4
    assert any('Pixel-verified correction' in ''.join(n.itertext()) for n in disclosures)
    assert all(printed not in ''.join(n.itertext()) for n in disclosures)


def test_fallback_pixel_read_repairs_ocr_misspelling_and_omitted_pronoun():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    before='After setding on the bench we read the clear sample today.'
    printed='After settling on the bench we I read the clear sample today.'
    lexicon=set(printed.lower().replace('.','').split())
    result=refine_fallback(before.split(),words(printed),lexicon,source=words(printed))
    assert result['tokens']==printed.split()
    assert len([d for d in result['decisions'] if d['status']=='RESOLVED'])==2
    assert result['marked']=={1,6}


def test_dictionary_cannot_delete_a_real_word_or_rewrite_an_unfamiliar_name():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    text='We saw Brindle under the clear bridge on Tuesday.'
    result=refine_fallback(text.split(),words('We saw bundle under the bridge on Tuesday.'),{'bundle','the'},source=words(text))
    assert result['tokens']==text.split()
    assert all(d['status']=='UNRESOLVED' for d in result['decisions'])


def test_multiple_note_references_keep_their_own_lexical_boundaries():
    text='Clear <n:first> words <n:second> remain readable.'
    result=adjudicate(text.split(),words('Clear words remain readable.'),
                      words('Clear words remain readable.'))
    assert result['tokens']==text.split()


@pytest.mark.parametrize('source_confidence',[30,99])
def test_agreeing_words_do_not_erase_unresolved_reader_uncertainty(source_confidence):
    text='The clear sample remains readable today.'
    result=adjudicate(text.split(),words(text,source_confidence),words(text,30),uncertain={3})
    assert result['marked']==({3}if source_confidence<90 else set())
    if source_confidence<90:assert result['word_notes'][3]['status']=='UNRESOLVED'


def test_unique_spelling_in_a_second_copy_cannot_settle_the_first_occurrence():
    source=words('The sample remains clear today.')
    witness=words('The sample rernains clear today. The sample becomes clear today.')
    result=adjudicate('The sample becomes clear today.'.split(),source,witness)
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['boxes'][2]==source[2]['bbox']


def test_matching_layer_and_first_read_do_not_hide_a_pixel_conflict():
    from cps.services.reflow.pixel_adjudication import audit_agreement
    original=words('The sample remalns readable today.')
    witness=words('The sample remains readable today.')
    notes=audit_agreement([w['text'] for w in original],original,witness)
    assert set(notes)=={2}
    assert notes[2]['status']=='UNRESOLVED'
    assert notes[2]['alternative']==['remains']


def test_fragment_binding_keeps_repeated_words_in_their_own_positions():
    from cps.services.reflow.pixel_adjudication import fragment_word_ranges
    fragment='<p>Clear sample then another clear sample.</p>'
    tokens='Clear sample then another clear sample.'.split()
    ranges=fragment_word_ranges(fragment,tokens)
    assert ranges is not None
    assert fragment[ranges[4][0]:ranges[4][1]]=='clear'
    assert ranges[4][0]>ranges[0][1]
    assert fragment_word_ranges(fragment,['different']) is None


def test_fragment_binding_keeps_inline_ordinals_and_typed_references_intact():
    from cps.services.reflow.pixel_adjudication import fragment_word_ranges
    fragment='<p>The 1<sup>st</sup> clear sample has a printed reference<a class="noteref" href="#note"><sup>7</sup></a>.</p>'
    tokens='The 1st clear sample has a printed reference7.'.split()
    ranges=fragment_word_ranges(fragment,tokens)
    assert ranges is not None
    assert fragment[slice(*ranges[2])]=='clear'
    assert fragment[slice(*ranges[1])]=='1<sup>st</sup>'
    assert fragment[slice(*ranges[7])]=='reference<a class="noteref" href="#note"><sup>7</sup></a>.'
    assert fragment_word_ranges(fragment,tokens[:-1]+['reference8.']) is None


def test_native_conflict_next_to_inline_ordinal_keeps_live_text_and_style(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import re
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    text='The 1st clear sample remalns readable today.'
    fragment='<p>The 1<sup>st</sup> clear sample remalns readable today.</p>'
    class Package:
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=500)
        leaf.insert_text((30,70),text,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=88)for w in leaf.get_text('words')]
        witness=[dict(w,text='remains'if w['text']=='remalns'else w['text'])for w in source]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        page=dict(pno=0,body=[fragment],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,55,390,80]])]
        vp._audit_native_pixels(doc,page,records,tmp_path,Package(),{})
    assert '1<sup>st</sup>'in page['body'][0]
    assert page['body'][0].count('class="source-disclosure-ref"')==1
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    assert_flagged_print(page['body'][0],['remalns'])
    authored=re.sub(r'<div class="reflow-unit-print">.*?</div>','',page['body'][0])
    authored=re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',authored)
    authored=re.sub(r'<span class="reflow-flagged-reading"><span class="reflow-plausible">(.*?)</span>.*?</span>',r'\1',authored)
    authored=re.sub(r'<span id="r-v4-[^"]+"></span>','',authored)
    authored=re.sub(r'<a[^>]*title="Compare this reading with original print"[^>]*>(.*?)</a>',r'\1',authored)
    assert authored==fragment,'Only generated controls and appended print are excluded; the source word and ordinal style stay exact'
    assert sum('Unresolved source reading: remalns.'in aside for aside in page['asides'])==1
    assert records[0]['marked']==[4]


def test_native_layer_pixel_conflict_is_visible_at_its_own_occurrence(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from tests.unit import test_round4_defects as ink
    text='The clear sample remalns readable beside the window today.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=500)
        page.insert_text((30,70),text,fontsize=10)
        original=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        witness=[dict(w,text='remains' if w['text']=='remalns'else w['text'],confidence=88)for w in original]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        reader=SimpleNamespace(cache=tmp_path/'cache'/'readings',key=None)
        output=tmp_path/'native.epub'
        result=vp.convert(doc,str(output),reader)
    record=result['pages'][0]
    assert record['source']=='native'
    decisions=[d for b in record['blocks']for d in b.get('adjudication',[])]
    assert any(d.get('alternative')==['remains']for d in decisions)
    import zipfile
    with zipfile.ZipFile(output)as archive:
        html=''.join(archive.read(n).decode()for n in archive.namelist()if n.endswith('.xhtml'))
    assert 'Unresolved source reading: remalns.' in html
    assert 'class="source-disclosure-ref"' in html
    assert 'images/v4-native-d-' in html


def test_unique_high_confidence_pixel_agreement_settles_unchanged_uncertain_word():
    text='The clear sample remains readable today.'
    result=adjudicate(text.split(),words(text),words(text),uncertain={3})
    assert result['tokens']==text.split()
    assert result['marked']==set()
    assert result['verified_unchanged']==[3]


def test_native_ocr_misspelling_is_corrected_only_with_contextual_pixel_support(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf,zipfile
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    text='The clear sample remalns readable beside the window today.'
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=500)
        page.insert_text((30,70),text,fontsize=10)
        witness=[dict(text='remains' if w[4]=='remalns' else w[4],bbox=list(w[:4]),confidence=99)
                 for w in page.get_text('words')]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        output=tmp_path/'native.epub'
        report=vp.convert(doc,str(output),SimpleNamespace(cache=tmp_path/'cache/readings',key=None))
    with zipfile.ZipFile(output) as archive:
        html=''.join(archive.read(n).decode()for n in archive.namelist()if n.endswith('.xhtml'))
    assert 'sample remains' in html
    assert 'Pixel-verified correction: remalns → remains.' in html
    assert any(d['status']=='RESOLVED'for b in report['pages'][0]['blocks']for d in b.get('adjudication',[]))


def test_fallback_cannot_borrow_the_clear_word_from_another_occurrence():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    text='The sample remalns clear today.'
    result=refine_fallback(text.split(),words('The sarnple rernains clear today. The sample remains clear today.'),
        {'the','sample','remains','clear','today'},source=words(text))
    assert result['tokens']==text.split()
    assert all(d['status']=='UNRESOLVED'for d in result['decisions'])


def test_adjacent_disjoint_row_cannot_certify_the_source_occurrence():
    from cps.services.reflow.pixel_adjudication import _same_occurrence
    assert not _same_occurrence(dict(bbox=[0,0,30,14]),dict(bbox=[0,16,30,22]))


def test_native_factory_joined_wrap_still_receives_an_independent_pixel_read(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=500)
        page.insert_text((30,70),'The sam-',fontsize=10)
        page.insert_text((30,85),'ple remains clear today.',fontsize=10)
        physical=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        regions=[vp.vision_reading.union([w['bbox']for w in physical[:2]]),
                 vp.vision_reading.union([w['bbox']for w in physical[2:]])]
        seen=[]
        def witness(*args):
            seen.append(args[1]);return physical
        monkeypatch.setattr(pa,'read_crop',witness)
        records=[dict(id='wrapped',role='body',source_regions=regions)]
        published=dict(pno=0,body=['<p>The sample remains clear today.</p>'],asides=[])
        vp._audit_native_pixels(doc,published,records,tmp_path,object(),{})
        assert seen==[regions]
        assert published['body']==['<p>The sample remains clear today.</p>']
        assert records[0]['published_tokens']=='The sample remains clear today.'.split()


def test_punctuation_mark_does_not_reclassify_a_source_note_as_garbled(tmp_path):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='Brindle Wembury, Clatterford Pemberley, 9-10.'
    source=words(text)
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=300,height=400)
        page,report=vp.compile_page({'blocks':[block(text.replace('9-10','9–10'),
            role='footnote',marker='3',bbox=(0,0,1000,1000))]},source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source)
    assert 'Brindle Wembury, Clatterford Pemberley,' in page['asides'][0]
    assert report['marks']==1
    assert '9–10.' in page['asides'][0]


def test_local_numeral_correction_preserves_original_occurrence_and_uncertainty(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='Please read section 11 before opening the box.'
    original=words(text)
    witness=[dict(w,text='III' if w['text']=='11' else w['text'])for w in original]
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=300,height=400)
        page,report=vp.compile_page({'blocks':[block(text,role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            [],SourceDisplay(doc,0),0,Package(),{},pixel_words=original,local_ocr=True,
            local_ocr_units=[dict(id='b1',words=original,source_word_indices=list(range(len(original))))],
            note_context={'cache':tmp_path/'cache'})
    assert report['blocks'][0]['published_tokens'][3]=='III'
    assert report['local_ocr_units'][0]['words'][3]['text']=='11'
    assert 'class="uncertain"' in page['asides'][0]
    assert any('11 → III.' in aside for aside in page['asides'][1:])
    assert any(d['status']=='RESOLVED'for d in report['blocks'][0]['adjudication'])


def test_model_formatting_tags_are_not_published_as_printed_words(tmp_path):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    original=words('Please read the clear sample today.')
    answer={'blocks':[block('Please read <i>the clear sample</i> today.',bbox=(0,0,1000,1000))]}
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=300,height=400)
        page,report=vp.compile_page(answer,original,SourceDisplay(doc,0),0,Package(),{},pixel_words=original)
    assert report['blocks'][0]['published_tokens']=='Please read the clear sample today.'.split()
    assert '&lt;i&gt;' not in ''.join(page['body'])
    assert answer['blocks'][0]['text']=='Please read <i>the clear sample</i> today.'
    assert '<i>the' in report['blocks'][0]['original_model_tokens']


def test_unproved_model_deletion_keeps_source_words_and_a_word_level_warning():
    text='The clear sample carries Brindle beside the window today.'
    result=adjudicate('The clear sample carries beside the window today.'.split(),words(text),words(text,30))
    assert result['tokens']==text.split()
    assert result['word_notes'][4]['status']=='UNRESOLVED'
    assert result['word_notes'][4]['chosen']==['Brindle']
    assert result['boxes'][4]==words(text)[4]['bbox']


def test_missing_ocr_word_cannot_prove_an_insertion_across_disjoint_source_crops():
    text='The clear sample carries Brindle beside the window today.'
    source=words(text)
    witness=[w for w in source if w['text']!='Brindle']
    result=adjudicate(text.split(),witness,witness)
    assert result['tokens']==text.split()
    assert result['word_notes'][4]['status']=='UNRESOLVED'


def test_blank_evidence_checks_intervening_ink_and_same_crop():
    import pymupdf
    from cps.services.reflow.source_display import SourceDisplay
    from cps.services.reflow.pixel_adjudication import blank_between
    with pymupdf.open() as doc:
        page=doc.new_page(width=300,height=100)
        page.insert_text((80,40),'Brindle',fontsize=12)
        display=SourceDisplay(doc,0)
        left=dict(bbox=[30,25,60,45]);right=dict(bbox=[140,25,170,45])
        assert not blank_between(display,left,right,[[30,25,60,45],[140,25,170,45]])
        assert not blank_between(display,left,right,[[30,25,170,45]])
        assert not blank_between(display,dict(bbox=[30,60,60,80]),dict(bbox=[140,60,170,80]),[[30,60,60,80],[140,60,170,80]])
        assert blank_between(display,dict(bbox=[30,60,60,80]),dict(bbox=[140,60,170,80]),[[30,60,170,80]])


def test_word_correction_note_explains_evidence_and_keeps_its_source_owner(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,text_quality
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='We carried the rare Brindlc sample beside the quiet window today.'
    original=words(text)
    monkeypatch.setattr(pa,'read_crop',lambda *args:[dict(w,text='Brindle' if w['text']=='Brindlc' else w['text'])for w in original])
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=400)
        page,report=vp.compile_page({'blocks':[block(text.replace('Brindlc','Brindle'),role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            original,SourceDisplay(doc,0),0,Package(),{},pixel_words=original,note_context={'cache':tmp_path/'cache'})
    assert any('Brindlc → Brindle.' in aside for aside in page['asides'])
    report['source']='vision'
    assert vp._compiled_text_quality([page],{0:report},[0])['passed']
    parts=text_quality.publication_parts(page)
    correction=next(p for p in parts if 'Brindlc → Brindle.' in p['text'])
    assert correction['owners']==[dict(page=0,block='b1')]
    assert 'An independent upright crop supports this correction.' in correction['text']


def test_fallback_adjudication_retains_the_first_reader_alternative(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='Please read the clear small wooden bridge beside the quiet stream before opening the box today.'
    witness=words(text)
    baseline=witness[:3]+witness[11:]
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=600,height=400)
        page,report=vp.compile_page({'blocks':[block(text,role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            baseline,SourceDisplay(doc,0),0,Package(),{},pixel_words=baseline,note_context={'cache':tmp_path/'cache'})
    assert report['blocks'][0]['published_tokens']==text.split()
    decisions=report['blocks'][0]['adjudication']
    assert any(d['model']==text.split()[3:11] and d['chosen']==d['model'] and d['status']=='RESOLVED'for d in decisions)


def test_unplaced_multiword_edit_has_bounded_word_notes_not_repeated_paragraphs(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    original=words('Please carry the blue box beside the quiet window today.')
    model='Please carry the small wooden sample and open box beside the quiet window today.'
    monkeypatch.setattr(pa,'read_crop',lambda *args:words(model,30))
    result=adjudicate(model.split(),original,words(model,30))
    assert all(len(note['chosen'])==1 for note in result['word_notes'].values())
    assert any(note.get('mapping')=='unplaced_within_span'for note in result['word_notes'].values())
    assert any(len(d['model'])>1 for d in result['decisions'])
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=600,height=400)
        page,report=vp.compile_page({'blocks':[block(model,role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            original,SourceDisplay(doc,0),0,Package(),{},pixel_words=original,note_context={'cache':tmp_path/'cache'})
    assert report['blocks'][0]['published_tokens']==[w['text']for w in original]
    assert report['blocks'][0]['source_image_words']==[]
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    assert_flagged_print(''.join(page['asides'][:1]), ['blue'])
    notes=[aside for aside in page['asides'] if 'Unresolved source reading' in aside]
    assert len(notes)==1,'One original occurrence owns this unproved multiword rewrite'
    assert all('small wooden sample and open' not in aside for aside in notes)
    assert any('class="reflow-unit-print"'in aside and '<img 'in aside for aside in page['asides'])


@pytest.mark.parametrize('kind',['word','label','label-punctuation','glyphs'])
def test_native_emission_order_does_not_duplicate_physical_words(tmp_path,monkeypatch,kind):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='Please carry the clear sample beside our quiet window today.'
    physical=[dict(w,bbox=[w['bbox'][0]+20,w['bbox'][1],w['bbox'][2]+20,w['bbox'][3]])for w in words(text)]
    if kind=='word':native=physical[:5]+physical[6:]+physical[5:6]
    elif kind.startswith('label'):
        native=physical+[dict(text='2}' if kind=='label-punctuation' else '2',bbox=[2,20,10,32])]
    else:
        native=physical[:3]+[dict(text='cl',bbox=[80,20,86,32])]+physical[4:]+[
            dict(text='ea',bbox=[86,20,93,32]),dict(text='r',bbox=[93,20,98,32])]
    monkeypatch.setattr(pa,'read_crop',lambda *args:physical)
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=600,height=400)
        page,report=vp.compile_page({'blocks':[block(text,role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            native,SourceDisplay(doc,0),0,Package(),{},pixel_words=physical,note_context={'cache':tmp_path/'cache'})
    assert report['blocks'][0]['published_tokens']==text.split()
    assert page['asides'][0].count('<p>2 ')==1


def test_reader_formatting_metadata_preserves_the_uncertain_word_index():
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    original=words('Please read the clear sample today.')
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=400)
        _,report=vp.compile_page({'blocks':[block('Please <i> read </i> the clear sample today.',low=[2],bbox=(0,0,1000,1000))]},
            [],SourceDisplay(doc,0),0,Package(),{},pixel_words=original)
    assert report['blocks'][0]['published_tokens']==[w['text']for w in original]
    assert report['blocks'][0]['marked']==[1]


def test_local_heading_case_and_name_can_follow_two_same_occurrence_pixel_reads():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    original=words('The cLeAr sAmPlE FROM Brindlc')
    witness=words('THE CLEAR SAMPLE FROM Brindle')
    result=refine_fallback([w['text'] for w in original],witness,
        {'the','clear','sample','from'},source=original,corroboration=witness)
    assert result['tokens']==[w['text'] for w in witness]
    assert all(d['status']=='RESOLVED' for d in result['decisions'])
    assert result['marked']


def test_local_unknown_name_cannot_follow_a_different_occurrence_pixel_read():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    original=words('The clear sample from Brindlc remains here today.')
    witness=words('The clear sample from Brindle remains here today.')
    remote=[dict(w,bbox=[x+400 for x in w['bbox']]) for w in witness]
    result=refine_fallback([w['text'] for w in original],witness,
        {'the','clear','sample','from','remains','here','today'},source=original,corroboration=remote)
    assert result['tokens']==[w['text'] for w in original]
    assert any(d['status']=='UNRESOLVED' for d in result['decisions'])


def test_local_two_view_name_repair_is_live_but_keeps_original_words_and_uncertainty(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='The clear sample from Brindlc remains here today.'
    original=words(text)
    witness=[dict(w,text='Brindle' if w['text']=='Brindlc' else w['text'])for w in original]
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=400)
        page,report=vp.compile_page({'blocks':[block(text,role='footnote',marker='2',bbox=(0,0,1000,1000))]},
            [],SourceDisplay(doc,0),0,Package(),{},pixel_words=witness,local_ocr=True,
            local_ocr_units=[dict(id='b1',words=original,source_word_indices=list(range(len(original))))],
            note_context={'cache':tmp_path/'cache'})
    assert report['blocks'][0]['published_tokens'][4]=='Brindle'
    assert report['local_ocr_units'][0]['words'][4]['text']=='Brindlc'
    assert 'class="uncertain"' in page['asides'][0]
    assert any('Brindlc → Brindle.' in aside for aside in page['asides'][1:])


def test_one_word_pixel_choice_survives_a_neighbouring_punctuation_conflict():
    source=words('The clear sample carries Brindle beside the window today.')
    witness=[dict(w,text='beside,' if w['text']=='beside' else w['text'])for w in source]
    result=adjudicate('The clear sample carries Brindlc beside the window today.'.split(),source,witness)
    assert result['tokens'][4]=='Brindle'
    assert result['word_notes'][4]['status']=='RESOLVED'
    assert result['word_notes'][4]['chosen']==['Brindle']
    assert 4 in result['marked']


def test_one_confident_word_is_settled_without_certifying_its_damaged_neighbour():
    source=words('Please read Brindle Calleton beside the quiet window today.')
    witness=[dict(w,confidence=30 if w['text']=='Calleton' else 99)for w in source]
    result=adjudicate('Please read Brindlc Calletqn beside the quiet window today.'.split(),source,witness)
    assert result['tokens'][2:4]==['Brindle','Calletqn']
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['word_notes'][3]['status']=='UNRESOLVED'


def test_two_pixel_views_can_settle_a_third_spelling_at_the_original_occurrence():
    source=words('Please read Brindlc beside the quiet window today.')
    witness=[dict(w,text='Brindle' if w['text']=='Brindlc' else w['text'])for w in source]
    model='Please read Brindlq beside the quiet window today.'.split()
    unproved=adjudicate(model,source,witness)
    result=adjudicate(model,source,witness,corroboration=witness)
    assert unproved['tokens'][2]=='Brindlq'
    assert result['tokens'][2]=='Brindle'
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['word_notes'][2]['source']==['Brindlc']
    assert result['word_notes'][2]['model']==['Brindlq']


def test_pixel_word_boundary_survives_an_adjacent_unresolved_punctuation_read():
    source=words('Please read clear bridge beside the quiet window today.')
    witness=[dict(w,text='beside,' if w['text']=='beside' else w['text'])for w in source]
    result=adjudicate('Please read clearbridge beside the quiet window today.'.split(),source,witness)
    assert result['tokens'][2:4]==['clear','bridge']
    assert all(i in result['marked'] for i in (2,3))
    assert any(d['status']=='RESOLVED' and d['chosen']==['clear','bridge']for d in result['decisions'])


def test_native_unknown_spelling_uses_two_pixel_reads_and_remains_marked(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf,zipfile
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,vision_pixels
    text='The clear sample from Brindlc remains beside the quiet window today.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=500,height=500)
        page.insert_text((30,70),text,fontsize=10)
        original=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        witness=[dict(w,text='Brindle' if w['text']=='Brindlc'else w['text'])for w in original]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        monkeypatch.setattr(vision_pixels,'words',lambda *args:witness)
        output=tmp_path/'native.epub'
        result=vp.convert(doc,str(output),SimpleNamespace(cache=tmp_path/'cache'/'readings',key=None))
    assert result['pages'][0]['source']=='native'
    assert any(d['status']=='RESOLVED' and d['chosen']==['Brindle']
               for b in result['pages'][0]['blocks']for d in b.get('adjudication',[]))
    with zipfile.ZipFile(output)as z:
        html=''.join(z.read(n).decode()for n in z.namelist()if n.endswith('.xhtml'))
    assert 'Brindlc → Brindle.' in html
    assert 'Pixel-verified correction:' in html


@pytest.mark.parametrize('break_char',['\u00ad','-'])
def test_pixel_word_boundary_does_not_split_a_printed_wrap(break_char):
    source=words('Please read clear'+break_char+' bridge beside the quiet window today.')
    witness=[dict(w,text=w['text'].rstrip('\u00ad-'))for w in source]
    result=adjudicate('Please read clearbridge beside the quiet window today.'.split(),source,witness)
    assert result['tokens'][2]=='clearbridge'
    assert result['tokens'][3]=='beside'


@pytest.mark.parametrize('accented',['cafélane','cafe\u0301lane'])
def test_pixel_word_boundary_keeps_the_original_model_diacritics(accented):
    source=words('Please read cafe lane beside the quiet window today.')
    result=adjudicate(['Please','read',accented,'beside','the','quiet','window','today.'],source,source)
    assert result['tokens'][2:4]==[accented[:-4],'lane']
    assert all(i in result['marked']for i in (2,3))
    assert any(d['status']=='UNRESOLVED'for d in result['decisions'])


@pytest.mark.parametrize('pixel',['cafe','cafè'])
def test_latin_pixel_alternative_cannot_certify_a_different_accent(pixel):
    source=words('Please read cafe beside the quiet window today.')
    witness=[dict(w,text=pixel if w['text']=='cafe'else w['text'])for w in source]
    result=adjudicate('Please read café beside the quiet window today.'.split(),source,witness,corroboration=witness)
    assert result['tokens'][2]=='café'
    assert result['word_notes'][2]['status']=='UNRESOLVED'


def test_printed_separate_words_do_not_fuse_across_a_row_change():
    source=words('Please read clear bridge beside the quiet window today.')
    source[3]['bbox']=[0,40,18,52]
    result=adjudicate('Please read clearbridge beside the quiet window today.'.split(),source,source)
    assert result['tokens'][2:4]==['clear','bridge']
    assert result['tokens'][4]=='beside'
    assert result['decisions'][0]['status']=='RESOLVED'


def test_local_two_pixel_views_cannot_certify_accent_absence():
    from cps.services.reflow.pixel_adjudication import refine_fallback
    source=words('Please read café beside the quiet window today.')
    witness=[dict(w,text='cafe'if w['text']=='café'else w['text'])for w in source]
    result=refine_fallback([w['text']for w in source],witness,{'read','cafe'},source=source,corroboration=witness)
    assert result['tokens'][2]=='café'
    assert result['word_notes'][2]['status']=='UNRESOLVED'


def test_native_footnote_receives_its_own_independent_pixel_audit(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf,zipfile
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    with pymupdf.open()as doc:
        page=doc.new_page(width=500,height=500)
        page.insert_text((30,70),'The clear sample remains readable beside the quiet window today.',fontsize=11)
        page.insert_text((355,65),'1',fontsize=7)
        page.insert_text((30,440),'1 The clear sample remalns readable beside the quiet window today.',fontsize=8)
        original=[dict(text=w[4],bbox=list(w[:4]),confidence=88)for w in page.get_text('words')]
        witness=[dict(w,text='remains'if w['text']=='remalns'else w['text'])for w in original]
        monkeypatch.setattr(pa,'read_crop',lambda display,regions,cache:[w for w in witness if any(vp.owned_words([w],b)for b in regions)])
        output=tmp_path/'native-note.epub'
        result=vp.convert(doc,str(output),SimpleNamespace(cache=tmp_path/'cache'/'readings',key=None))
    assert result['pages'][0]['source']=='native'
    with zipfile.ZipFile(output)as z:
        html=''.join(z.read(n).decode()for n in z.namelist()if n.endswith('.xhtml'))
    assert 'class="footnote"' in html
    assert 'Unresolved source reading: remalns.' in html
    assert any(d.get('alternative')==['remains']for b in result['pages'][0].get('native_note_adjudication',[])for d in b.get('adjudication',[]))


def test_native_note_quality_failure_keeps_its_registered_note_owner(monkeypatch):
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,unit_recovery
    monkeypatch.setattr(unit_recovery,'current',lambda:SimpleNamespace(check_units=lambda units:None,fragment_owners=lambda fragment:[dict(page=0,block='native-0')]))
    good='The clear sample remains readable beside the quiet window today. '*20
    bad=' '.join('qxv'+str(i)+'zrt'for i in range(50))
    page=dict(pno=0,body=['<p><span data-source-page="0" data-source-block="native-0">'+good+'</span></p>'],asides=['<aside class="footnote"><p><span data-source-page="0" data-source-block="native-note-0">'+bad+'</span></p></aside>'])
    records={0:dict(source='native',blocks=[dict(id='native-0',role='body')],native_note_adjudication=[dict(id='native-note-0',role='footnote')])}
    with pytest.raises(unit_recovery.UnitFailure)as error:vp._compiled_text_quality([page],records,[0])
    assert error.value.units==[dict(page=0,block='native-note-0')]


@pytest.mark.parametrize('empty_box',[False,True])
def test_native_continued_note_binds_unique_text_without_changing_quotes(tmp_path,monkeypatch,empty_box):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,assemble,build_epub
    text='The reader\'s clear sample says "readable" beside the quiet window.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=500,height=500);page.insert_text((30,70),text,fontsize=10)
        witness=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        box=(0,0,0,0)if empty_box else vp.vision_reading.union([w['bbox']for w in witness])
        note=assemble.Note(num=None,text=text,pno=0,bbox=box)
        book=assemble.Book(notes=[note],pages={0:[]})
        fragment=build_epub._aside_html(note,{},set())
        page_data=dict(pno=0,body=[],asides=[fragment])
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        result=vp._audit_native_note_pixels(doc,book,page_data,tmp_path/'cache',None,{})
    assert len(result)==1 and result[0]['published_tokens']==text.split()
    assert vp._seam_text(page_data['asides'][0])==text
    assert 'data-source-block="native-note-0"'in page_data['asides'][0]


def test_native_mixed_glyph_note_cannot_bypass_live_word_audit(tmp_path):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,assemble
    with pymupdf.open()as doc:
        doc.new_page(width=300,height=300)
        note=assemble.Note(num=1,text='Ordinary sample A ends.',pno=0,bbox=(20,20,220,50),glyph_fallback=True,
            glyph_runs=[('t','Ordinary sample '),('glyph','A'),('t',' ends.')])
        with pytest.raises(ValueError,match='glyph'):
            vp._audit_native_note_pixels(doc,assemble.Book(notes=[note]),dict(pno=0,body=[],asides=[]),tmp_path,None,{})


def test_native_note_word_equal_to_label_binds_only_its_lexical_copy(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,assemble,build_epub
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=300);page.insert_text((30,70),'1 1',fontsize=10)
        witness=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        note=assemble.Note(num=1,text='1',pno=0,bbox=vp.vision_reading.union([w['bbox']for w in witness]))
        fragment=build_epub._scope_ids(build_epub._aside_html(note,{'1':'ref'}, {'1'}),0)
        page_data=dict(pno=0,body=[],asides=[fragment])
        monkeypatch.setattr(pa,'read_crop',lambda display,regions,cache:[w for w in witness if any(vp.owned_words([w],b)for b in regions)])
        result=vp._audit_native_note_pixels(doc,assemble.Book(notes=[note]),page_data,tmp_path,None,{})
    assert len(result)==1 and result[0]['published_tokens']==['1']
    assert '<a class="note-return" href="#ref" aria-label="Return to note reference">1</a>'in page_data['asides'][0]
    import json
    from xml.etree import ElementTree
    tree=ElementTree.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+page_data['asides'][0]+'</root>')
    spans=[node for node in tree.iter()if node.get('data-source-block')=='native-note-0']
    assert len(spans)==1 and ''.join(spans[0].itertext())=='1'
    assert json.loads(spans[0].get('data-source-regions'))==[witness[1]['bbox']]


def test_inconclusive_second_read_cannot_restore_rejected_model_paragraph(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='The genuine printed paragraph follows quiet rivers past ordinary villages before sunset.'
    invented='Imaginary violet machines announce impossible discoveries beneath a fictional palace every evening.'
    source=words(text)
    monkeypatch.setattr(pa,'read_crop',lambda *args:words(text,30))
    class Package:
        def image(self,*args):pass
    with pymupdf.open() as doc:
        doc.new_page(width=600,height=400)
        page,report=vp.compile_page({'blocks':[block(invented,bbox=(0,0,1000,1000))]},
            source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source,note_context={'cache':tmp_path/'cache','labels':{},'owners':{},'catalogue':{},'refs':{}})
    record=report['blocks'][0]
    assert record['published_tokens']==text.split()
    assert record['original_model_tokens']==invented.split()
    assert any(d['status']=='UNRESOLVED' and d['chosen']==d['source'] and d['model']
               for d in record['adjudication'])
    assert record['marked']


def test_marker_lexical_view_reserves_only_the_proved_physical_occurrence():
    import copy
    from cps.services.reflow import vision_pipeline as vp
    source=[dict(text='Clear',bbox=[10,20,40,32]),
            dict(text='7',bbox=[41,17,45,23]),
            dict(text='words',bbox=[47,20,77,32]),
            dict(text='7',bbox=[150,20,155,32])]
    before=copy.deepcopy(source)
    tokens=['Clear','<n:first>','words','7']
    proofs={1:dict(status='PROVED',label='7',source_bbox=[41,17,45,23])}
    model,lexical=vp._lexical_marker_view(tokens,source,proofs)
    assert model==tokens and [w['text']for w in lexical]==['Clear','words','7']
    assert lexical[-1]['bbox']==[150,20,155,32]
    assert source==before
    # A genuinely separate printed slot remains a separate reference.
    tokens=['Clear','<n:first>','words','<n:second>']
    proofs[3]=dict(status='PROVED',label='7',source_bbox=[150,20,155,32])
    model,lexical=vp._lexical_marker_view(tokens,source,proofs)
    assert model==tokens and [w['text']for w in lexical]==['Clear','words']


def test_marker_lexical_view_strips_only_an_exact_proved_raised_suffix():
    import copy
    from cps.services.reflow import vision_pipeline as vp
    source=[dict(text='Clear7,',bbox=[10,20,50,32]),
            dict(text='words',bbox=[55,20,85,32]),
            dict(text='Clear7,',bbox=[150,20,190,32])]
    before=copy.deepcopy(source)
    tokens=['Clear7,','<n:first>','words','Clear7,']
    proof=dict(status='PROVED',label='7',source_bbox=[45,17,49,24],
               deduplicated_literal_token=0,literal_display_text='Clear,')
    model,lexical=vp._lexical_marker_view(tokens,source,{1:proof})
    assert model==['Clear,','<n:first>','words','Clear7,']
    assert [w['text']for w in lexical]==['Clear,','words','Clear7,']
    assert lexical[0]['bbox']==source[0]['bbox']
    assert tokens==['Clear7,','<n:first>','words','Clear7,'] and source==before


def test_unbound_or_numeric_role_unproved_markers_are_never_stripped():
    from cps.services.reflow import vision_pipeline as vp
    source=[dict(text='Clear',bbox=[10,20,40,32]),
            dict(text='7',bbox=[41,20,45,32]),dict(text='words',bbox=[47,20,77,32])]
    tokens=['Clear','<n:first>','words']
    for reason in [vp._MARKER_ROLE_UNPROVED,vp._MARKER_SLOT_UNBOUND]:
        proof=dict(status='UNPROVED',source_bbox=[41,20,45,32],reason=reason,
                   anchor_before_bbox=source[0]['bbox'],anchor_after_bbox=source[2]['bbox'])
        assert vp._lexical_marker_view(tokens,source,{1:proof})==(tokens,source)
    # An unbounded entire-block rectangle cannot reserve a source number.
    proof=dict(status='UNPROVED',source_bbox=[0,0,100,100],
               reason=vp._MARKER_GROUP_AMBIGUOUS)
    assert vp._lexical_marker_view(tokens,source,{1:proof})==(tokens,source)


def test_bounded_unknown_marker_identity_remains_opaque_without_claiming_a_label():
    from cps.services.reflow import vision_pipeline as vp
    source=[dict(text='Clear',bbox=[10,20,40,32]),
            dict(text='3*',bbox=[41,17,48,23]),dict(text='words',bbox=[50,20,80,32]),
            dict(text='3',bbox=[150,20,155,32])]
    tokens=['Clear','<n:first>','words','3']
    proof=dict(status='UNPROVED',source_bbox=[41,17,48,23],
               reason=vp._MARKER_GROUP_AMBIGUOUS,
               anchor_before_bbox=source[0]['bbox'],anchor_after_bbox=source[2]['bbox'])
    model,lexical=vp._lexical_marker_view(tokens,source,{1:proof})
    assert model==tokens and [w['text']for w in lexical]==['Clear','words','3']
    assert proof['status']=='UNPROVED' and 'label'not in proof
    # A letter-looking unknown reading is not safe to classify as marker text.
    assert vp._lexical_marker_view(tokens,[dict(source[1],text='B')],{1:proof})[1][0]['text']=='B'


@pytest.mark.parametrize('kind',['bare','suffix','opaque'])
def test_compile_reserves_proved_marker_before_word_conservation(tmp_path,monkeypatch,kind):
    import copy,pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='The clear sample remains readable near the ordinary village before sunset today.'
    source=words(text)
    glyph=[58,16,62,24];suffix=kind=='suffix'
    if suffix:source[2]=dict(source[2],text='sample7',bbox=[40,20,63,32])
    else:source.insert(3,dict(text='3*'if kind=='opaque'else'7',bbox=glyph,confidence=99))
    original=copy.deepcopy(source)
    proposed=text.replace('sample','sample7<n:n1>'if suffix else 'sample<n:n1>')
    expected=text.replace('sample','sample <n:n1>').split()
    calls=[];dispatches=[]
    def proof(tokens,optical,display,box,context,pno,literal=None):
        calls.append((context.get('marker_reader'),copy.deepcopy(optical)))
        i=tokens.index('<n:n1>')
        result=(dict(status='UNPROVED',source_bbox=glyph,reason=vp._MARKER_GROUP_AMBIGUOUS,
                     anchor_before_bbox=original[2]['bbox'],anchor_after_bbox=original[4]['bbox'])
                if kind=='opaque'else dict(status='PROVED',label='7',source_bbox=glyph))
        if tokens[i-1]=='sample7':result.update(deduplicated_literal_token=i-1,literal_display_text='sample')
        return {i:result}
    def dispatch(*args):dispatches.append(args);raise AssertionError('No added marker dispatch is allowed')
    monkeypatch.setattr(vp,'_placeholder_display_evidence',proof)
    monkeypatch.setattr(vp,'_literal_note_refs',lambda *args,**kwargs:{})
    monkeypatch.setattr(pa,'read_crop',lambda *args:copy.deepcopy(source))
    context=dict(cache=tmp_path/'cache',labels={'7':[(0,'n1')]},owners={},
                 catalogue={(0,'n1'):{'target':'v4-note-0-n1','label':'99'}},refs={},marker_reader=dispatch)
    class Package:
        def image(self,*args):pass
    with pymupdf.open()as doc:
        doc.new_page(width=600,height=400)
        page,report=vp.compile_page({'blocks':[block(proposed,bbox=(0,0,1000,700)),block('',ident='n1',role='footnote',marker='99',bbox=(0,900,1000,1000))]},
            source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source,note_context=context)
    assert report['blocks'][0]['published_tokens']==expected
    assert source==original and calls[-1][1]==original
    assert len(calls)==2 and calls[0][0]is None and calls[1][0]is dispatch
    assert not dispatches
    if kind=='opaque':
        assert report['note_marker_disclosures'] and report['note_marker_disclosures'][0]['status']=='DEGRADED_SOURCE_CONTEXT'
        assert 'class="source-disclosure-ref"'in page['body'][0]
    else:
        assert 'class="noteref"'in page['body'][0] and '<sup>7</sup>'in page['body'][0]
    assert not any(d.get('source')==['7']for d in report['blocks'][0]['adjudication'])


def test_pixel_audit_does_not_turn_missing_terminal_punctuation_into_a_word_conflict():
    from cps.services.reflow.pixel_adjudication import audit_agreement
    source=words('The clear sample remains readable today.')
    witness=[dict(w,text='today' if w['text']=='today.' else w['text'])for w in source]
    assert audit_agreement([w['text']for w in source],source,witness)=={}
    damaged=[dict(w,text='t0day' if w['text']=='today' else w['text'])for w in witness]
    assert set(audit_agreement([w['text']for w in source],source,damaged))=={5}


@pytest.mark.parametrize('recognized',['t','+'])
def test_unknown_reference_slot_is_not_an_ordinary_lexical_word(recognized):
    from cps.services.reflow import vision_pipeline as vp
    tokens=['Clear','minerals,’‡','<n:note>','and','ordinary','samples.']
    source=[dict(text='minerals,’',bbox=[30,20,75,32]),
            dict(text=recognized,bbox=[77,22,81,30]),dict(text='and',bbox=[84,20,102,32])]
    proof=dict(status='UNPROVED',source_bbox=[77,22,81,30],
        reason=vp._MARKER_SLOT_UNBOUND,
        anchor_before_bbox=source[0]['bbox'],anchor_after_bbox=source[2]['bbox'])
    model,lexical=vp._lexical_marker_view(tokens,source,{2:proof})
    assert model==['Clear','minerals,’','<n:note>','and','ordinary','samples.']
    assert [w['text']for w in lexical]==['minerals,’','and']
    assert source[1]['text']==recognized,'reservation must not edit the original occurrence inventory'
    assert proof['lexical_literal_suffix']=='‡'


def test_unplaced_terminal_symbol_is_not_removed_then_reintroduced_as_a_word():
    import copy
    from cps.services.reflow import vision_pipeline as vp
    tokens=['Clear','specimens*','<n:note>']
    source=[dict(text='Clear',bbox=[10,20,35,32]),
            dict(text='specimens*',bbox=[38,20,95,32])]
    original=copy.deepcopy(source)
    proof=dict(status='UNPROVED',source_bbox=[38,20,98,32],
        reason=vp._MARKER_GROUP_INCOMPLETE,
        anchor_before_bbox=source[-1]['bbox'],anchor_after_bbox=None)
    model,lexical=vp._lexical_marker_view(tokens,source,{2:proof})
    assert model==tokens
    assert lexical==source==original
    assert 'lexical_literal_suffix' not in proof
    assert 'lexical_placeholder' not in proof


@pytest.mark.parametrize('kind',['plain_pixels','attached_pixels','crop_omits_symbol'])
def test_unplaced_literal_survives_lexical_adjudication_exactly_once(tmp_path,monkeypatch,kind):
    import copy,pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    text='The clear sample remains readable near the ordinary village before sunset today.'
    source=words(text)
    if kind!='plain_pixels':source[2]=dict(source[2],text='sample*')
    witness=copy.deepcopy(source)
    if kind=='crop_omits_symbol':witness[2]['text']='sample'
    monkeypatch.setattr(pa,'read_crop',lambda *args:copy.deepcopy(witness))
    monkeypatch.setattr(vp,'_literal_note_refs',lambda *args,**kwargs:{})
    def proof(tokens,*args,**kwargs):
        return {tokens.index('<n:n1>'):dict(status='UNPROVED',source_bbox=[10,10,300,40],
            reason=vp._MARKER_SLOT_UNBOUND)}
    monkeypatch.setattr(vp,'_placeholder_display_evidence',proof)
    context=dict(cache=tmp_path/'cache',labels={'*':[(0,'n1')]},owners={},
        catalogue={(0,'n1'):{'target':'v4-note-0-n1','label':'*'}},refs={})
    class Package:
        def image(self,*args):pass
    with pymupdf.open()as doc:
        doc.new_page(width=600,height=400)
        _,report=vp.compile_page({'blocks':[block(text.replace('sample','sample*<n:n1>'),bbox=(0,0,1000,700)),
            block('',ident='n1',role='footnote',marker='*',bbox=(0,900,1000,1000))]},
            source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source,note_context=context)
    record=report['blocks'][0]
    assert record['published_tokens']==text.replace('sample','sample* <n:n1>').split()
    assert not record['marked'],'only glyph identity is uncertain; the unchanged lexical word is not a correction'
    assert not record['adjudication']
    assert report['note_marker_disclosures'],'the unproved reference still requires its own source disclosure'


def test_agreeing_word_pixel_audit_keeps_indices_across_typed_references():
    from cps.services.reflow.pixel_adjudication import audit_agreement
    source=words('Clear samples remain readable.')
    witness=[dict(w,text='samp1es' if w['text']=='samples' else w['text'])for w in source]
    notes=audit_agreement(['Clear','<n:note>','samples','remain','readable.'],source,witness)
    assert set(notes)=={2}
    assert notes[2]['source']==notes[2]['model']==notes[2]['chosen']==['samples']
    assert notes[2]['alternative']==['samp1es']
    assert notes[2]['status']=='UNRESOLVED'


def test_native_live_text_without_a_source_occurrence_cannot_skip_pixel_audit(tmp_path):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=400)
        page.insert_text((30,60),'A clear sample remains readable.')
        native=dict(pno=0,body=['<p>A clear sample remains readable.</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[])]
        with pytest.raises(ValueError,match='Native live text has no uniquely bound source occurrence'):
            vp._audit_native_pixels(doc,native,records,tmp_path,object(),{})


def test_agreeing_layer_and_reader_do_not_override_two_own_pixel_readings():
    from cps.services.reflow.pixel_adjudication import adjudicate
    original=words('The clear sample crosses quiet fie1ds before sunset today.')
    pixels=[dict(w,text='fields' if w['text']=='fie1ds' else w['text'])for w in original]
    result=adjudicate([w['text']for w in original],original,pixels,corroboration=[dict(w)for w in pixels])
    assert result['tokens']=='The clear sample crosses quiet fields before sunset today.'.split()
    assert 5 in result['marked']
    assert result['word_notes'][5]['source']==['fie1ds'] and result['word_notes'][5]['chosen']==['fields']
    assert result['word_notes'][5]['status']=='RESOLVED'
    remote=[dict(w,bbox=[w['bbox'][0],200,w['bbox'][2],212])for w in pixels]
    uncertain=adjudicate([w['text']for w in original],original,pixels,corroboration=remote)
    assert uncertain['tokens']==[w['text']for w in original]


@pytest.mark.parametrize('punctuation',[';', '.'])
def test_split_terminal_punctuation_does_not_create_a_word_or_duplicate_symbol(punctuation):
    source=words('The clear sample remains readable today '+punctuation)
    proposed=('The clear sample remains readable today'+punctuation).split()
    result=adjudicate(proposed,source,[dict(w)for w in source])
    assert result['tokens']==proposed
    assert result['marked']==set(),'terminal punctuation segmentation is not a word correction'
    damaged_source=[dict(w,text='t0day' if w['text']=='today' else w['text'])for w in source]
    damaged=adjudicate(proposed,damaged_source,[dict(w)for w in damaged_source])
    assert damaged['tokens'][-1].endswith(punctuation)
    assert 5 in damaged['marked'],'letter disagreement still needs its own uncertainty'


@pytest.mark.parametrize('notice_kind',['punctuation','source_context','source_raster'])
def test_generated_native_notice_is_not_an_unbound_authored_paragraph(tmp_path,monkeypatch,notice_kind):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf
    from cps.services.reflow import assemble,build_epub,vision_pipeline as vp,pixel_adjudication as pa
    text='The clear sample remains readable beside the quiet window.'
    with pymupdf.open()as doc:
        original=doc.new_page(width=500,height=500)
        original.insert_text((30,70),text,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in original.get_text('words')]
        element=assemble.Element('p',runs=[['t',text]],pno=0,bbox=[30,58,450,73])
        book=assemble.Book(elements=[element],pages={0:[element]})
        notice=(build_epub._punctuation_notice(0,0)if notice_kind=='punctuation'else
                build_epub._source_check_notice('Original source retained','original-p0000.xhtml#page','View original source'))
        if notice_kind=='source_raster':
            notice='<p><span class="source-raster"><img src="images/source.png" alt="Original source lettering"/></span></p>'
        native=dict(pno=0,body=['<p>'+text+'</p>',notice],asides=[])
        records=vp._native_seam_blocks(doc,book,0,native)
        assert records[0]['role']=='body' and records[0]['source_regions']
        assert records[1]['role']=='other' and records[1]['source_regions']==[]
        calls=[]
        monkeypatch.setattr(pa,'read_crop',lambda *args:(calls.append(args[1])or source))
        vp._audit_native_pixels(doc,native,records,tmp_path,object(),{})
        assert len(calls)==1 and native['body']==['<p>'+text+'</p>',notice]
        # Equal prose without the generated notice class remains subject to
        # normal physical ownership, even when its words describe a notice.
        unbound=dict(pno=0,body=['<p>Original source retained</p>'],asides=[])
        authored=vp._native_seam_blocks(doc,book,0,unbound)
        assert authored[0]['role']=='body'
        with pytest.raises(ValueError,match='Native live text has no uniquely bound source occurrence'):
            vp._audit_native_pixels(doc,unbound,authored,tmp_path,object(),{})


def test_partial_printed_hyphen_atom_cannot_certify_truncating_a_complete_word():
    source=words('Please inspect the printed wor-')
    proposed='Please inspect the printed workpieces:'.split()
    result=adjudicate(proposed,source,[dict(w)for w in source])
    assert result['tokens']==proposed
    assert result['word_notes'][4]['status']=='UNRESOLVED'
    assert not any(d['status']=='RESOLVED'for d in result['decisions'])


def test_unavailable_secondary_engine_returns_explicit_failure_without_dispatch(tmp_path,monkeypatch):
    from cps.services.reflow import pixel_adjudication as pa
    import shutil,subprocess
    monkeypatch.setattr(shutil,'which',lambda name:str(tmp_path/'missing-recognizer'))
    def forbidden(*args,**kwargs):
        raise AssertionError('Unavailable executable must not be dispatched')
    monkeypatch.setattr(subprocess,'run',forbidden)
    result=pa.read_crop(None,[[0,0,10,10]],tmp_path)
    assert result==[]
    assert result.failure=='RuntimeError: Independent OCR executable is unavailable'


def test_projection_keeps_generated_control_tails_at_their_original_siblings():
    from tests.unit.test_round4_defects import plain
    from xml.etree import ElementTree as ET
    node=ET.fromstring('<p xmlns="http://www.w3.org/1999/xhtml"><span>as<a>†</a> a</span><a>†</a> <span>quiet harbor.</span></p>')
    assert plain(node)=='as a quiet harbor.'


def test_rejected_first_read_cannot_remove_original_terminal_punctuation():
    original=words('The quiet river.')
    proposed='The quiet river'.split()
    result=adjudicate(proposed,original,words('The quiet river'),unresolved_source=True)
    assert result['tokens']==['The','quiet','river.']
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['word_notes'][2]['model']==['river']


def test_fused_recognition_needs_pixel_space_proof_to_clear_unchanged_words():
    from cps.services.reflow.pixel_adjudication import audit_agreement
    original=words('A distinct sample remains readable.')
    merged=dict(original[0],text='Adistinct',bbox=[10,20,25,30])
    witness=[merged,*original[2:]]
    tokens=[w['text']for w in original]
    assert set(audit_agreement(tokens,original,witness))=={0,1}
    observed=[]
    def blank(a,b):
        observed.append((a['text'],b['text']));return True
    assert audit_agreement(tokens,original,witness,boundary_proof=blank)=={}
    assert observed==[('A','distinct')]
    assert set(audit_agreement(tokens,original,witness,boundary_proof=lambda *args:False))=={0,1}


def test_separate_printed_words_keep_their_boundary_across_a_row_change():
    source=words('We read (cafe bleu beside the window.')
    for w in source[3:]:w['bbox']=[w['bbox'][0]-60,w['bbox'][1]+18,w['bbox'][2]-60,w['bbox'][3]+18]
    result=adjudicate('We read (cafébleu beside the window.'.split(),source,source)
    assert result['tokens']==['We','read','(café','bleu','beside','the','window.']
    assert result['decisions'][0]['method']=='pixel_word_boundary_only'
    assert result['decisions'][0]['status']=='UNRESOLVED'
    assert result['marked']=={2,3},'the accent still needs its own pixel proof'


@pytest.mark.parametrize('word',['<em>remalns</em>','re<em>malns</em>'])
def test_native_pixel_correction_preserves_inline_word_formatting(tmp_path,monkeypatch,word):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import re
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,vision_pixels
    fragment='<p>The clear sample '+word+' readable today.</p>'
    class Package:
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=500)
        leaf.insert_text((30,70),'The clear sample remalns readable today.',fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        witness=[dict(w,text='remains'if w['text']=='remalns'else w['text'])for w in source]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        monkeypatch.setattr(vision_pixels,'words',lambda *args:witness)
        page=dict(pno=0,body=[fragment],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,55,390,80]])]
        vp._audit_native_pixels(doc,page,records,tmp_path,Package(),{})
    authored=re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',page['body'][0])
    assert vp._seam_text(authored)=='The clear sample remains readable today.'
    assert '<em>'in authored and '</em>'in authored
    assert records[0]['marked']==[3]
    assert any(d['status']=='RESOLVED'and d['chosen']==['remains']for d in records[0]['adjudication'])


def test_alternate_optical_alphabet_settles_a_macron_without_erasing_its_mark():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear sampel beside the window.')
    model='Please read the clear samplē beside the window.'.split()
    witness=pa.CropWitness(words('Please read the clear sample beside the window.'))
    witness.alternates=lambda:[words('Please read the clear samplē beside the window.',96),
                               words('Please read the clear samplē beside the window.',95)]
    result=adjudicate(model,source,witness)
    assert result['tokens']==model
    assert result['word_notes'][4]['status']=='RESOLVED'
    assert result['word_notes'][4]['method']=='two_alternate_alphabet_pixel_readings'
    assert result['marked']=={4}


def test_alternate_alphabet_does_not_claim_absence_of_an_unrepresented_mark():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear sample beside the window.')
    witness=pa.CropWitness(source)
    witness.alternates=lambda:[source,source]
    result=adjudicate('Please read the clear samplẹ beside the window.'.split(),source,witness)
    assert result['word_notes'][4]['status']=='UNRESOLVED'
    assert result['tokens'][4]=='samplẹ'


def test_two_alternate_pixel_reads_restore_the_native_number_at_its_own_occurrence():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read section 4.2.17; before opening the box.')
    witness=pa.CropWitness(words('Please read section 4.2-17; before opening the box.',40))
    witness.alternates=lambda:[source,source]
    result=adjudicate('Please read section 4.2-17; before opening the box.'.split(),source,witness)
    assert result['tokens']==[w['text']for w in source]
    assert result['word_notes'][3]['status']=='RESOLVED'


def test_alternate_optical_data_must_match_the_pinned_network(tmp_path):
    from cps.services.reflow import pixel_adjudication as pa
    (tmp_path/'LatinRaw.traineddata').write_bytes(b'not the optical model')
    assert pa._verified_latin_directory((str(tmp_path),))is None


def test_settled_edit_does_not_run_the_optional_recognizer():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear sample beside the window.')
    witness=pa.CropWitness(source)
    def unwanted():raise AssertionError('an already proved edit needs no extra recognition')
    witness.alternates=unwanted
    result=adjudicate('Please read the clear sampel beside the window.'.split(),source,witness)
    assert result['tokens']==[w['text']for w in source]
    assert result['word_notes'][4]['status']=='RESOLVED'


def test_unsettled_alternate_word_publishes_its_own_ink_not_wrong_live_text(tmp_path,monkeypatch):
    from tests.unit import test_round4_defects as ink
    from cps.services.reflow import pixel_adjudication as pa
    page=ink.InkPage();printed='The clear sample remains readable beside the open window.'
    page.text(printed,'body',model=printed.replace('remains','remalns'))
    source=[dict(text=w[4],bbox=list(w[:4]),confidence=40)for w in page.page.get_text('words')]
    def crop(display,regions,cache):
        # The first witness and the proposed reading agree on the bad spelling;
        # two alternate pixel views disagree but cannot certify a replacement.
        bad=pa.CropWitness([dict(w,text='remalns'if w['text']=='remains'else w['text'])for w in source])
        bad.alternates=lambda:[source,source]
        return bad
    monkeypatch.setattr(pa,'read_crop',crop)
    _,report,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    text=' '.join(ink.body_texts(docs))
    assert 'remalns'not in text
    images=[n for root in docs.values()for n in root.iter()if n.get('class')=='source-word-image']
    assert len(images)==1
    assert len(images[0])==1 and images[0][0].tag.endswith('img')
    assert 'The clear sample'in text and 'readable beside the open window.'in text
    assert report['pages'][0]['blocks'][0]['source_image_words']==[]
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    flags=[n for root in docs.values()for n in root.iter()if n.get('class')=='reflow-plausible']
    assert [''.join(n.itertext())for n in flags]==['remains']
    assert all(d['status']=='UNRESOLVED'for d in report['pages'][0]['blocks'][0]['adjudication'])


@pytest.mark.parametrize('dot',[False,True])
def test_underdot_absence_requires_complete_blank_pixels_below_a_proved_baseline(dot):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=200,height=120)
        page.insert_text((30,50),'calm',fontsize=14)
        word=page.get_text('words')[0]
        if dot:page.draw_circle((46,52.5),.7,color=(0,0,0),fill=(0,0,0))
        assert pa.below_dot_absent(SourceDisplay(doc,0),dict(text='calm',bbox=list(word[:4])))is(not dot)


@pytest.mark.parametrize('dot',[False,True])
def test_alternate_alphabet_needs_pixels_to_remove_a_below_baseline_mark(dot):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=350,height=120)
        page.insert_text((30,50),'Please read calm beside the window.',fontsize=14)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        if dot:page.draw_circle((source[2]['bbox'][0]+8,52.5),.7,color=(0,0,0),fill=(0,0,0))
        witness=pa.CropWitness(source);witness.alternates=lambda:[source,source]
        witness.mark_absence=lambda word:pa.below_dot_absent(SourceDisplay(doc,0),word)
        result=adjudicate('Please read cạlm beside the window.'.split(),source,witness)
    assert result['tokens'][2]==('cạlm'if dot else'calm')
    assert result['word_notes'][2]['status']==('UNRESOLVED'if dot else'RESOLVED')


def test_unsettled_views_cannot_force_a_wrong_live_spelling_or_choose_each_other():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('clear sampel beside')
    witness=pa.CropWitness(source)
    witness.alternates=lambda:[words('clear sample beside',40),words('clear samble beside',35)]
    result=dict(tokens='clear sampel beside'.split(),marked={1},word_notes={})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert result['tokens'][1]=='sampel','no alternate guess gains authority'
    assert result['word_notes'][1]['source_image_only']
    assert result['word_notes'][1]['status']=='UNRESOLVED'


def test_installed_alternate_network_reads_printed_macron_pixels(tmp_path):
    import pymupdf,shutil
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    engine=shutil.which('tesseract')
    if not engine or not pa._latin_directory(engine):pytest.skip('pinned alternate optical data not installed')
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=160)
        font=pymupdf.Font('tiro')
        writer=pymupdf.TextWriter(page.rect)
        writer.append((25,65),'Please read māple beside the clear window.',font=font,fontsize=14)
        writer.write_text(page)
        witness=pa.read_crop(SourceDisplay(doc,0),[[20,45,380,75]],tmp_path)
        views=witness.alternates()
    assert len(views)==2
    assert all('māple'in [w['text']for w in view]for view in views)


def test_alternate_recognition_joins_only_consecutive_rows_in_the_same_column():
    from cps.services.reflow import pixel_adjudication as pa
    assert pa.alternate_regions([[20,20,180,32],[30,35,175,47],[30,50,160,62]])==[[20,20,180,62]]
    assert pa.alternate_regions([[20,20,100,32],[130,35,200,47]])==[[20,20,100,32],[130,35,200,47]]
    assert pa.alternate_regions([[20,20,180,32],[20,100,180,112]])==[[20,20,180,32],[20,100,180,112]]


def test_refinement_retains_an_unresolved_original_word_footprint():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the sampel beside the quiet window.')
    result=pa.refine_fallback([w['text']for w in source],
        words('Please read the sample beside the quiet window.'),{'unrelated'},source=source)
    assert result['tokens'][3]=='sampel'
    assert result['word_notes'][3]['status']=='UNRESOLVED'
    assert result['boxes'][3]==source[3]['bbox']


def test_a_punctuation_atom_cannot_authorize_deleting_a_word_prefix():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('read , beside')
    source[1]['bbox']=[35,20,38,32]
    view=words('read scope beside')
    witness=pa.CropWitness([dict(w,confidence=40)for w in source]);witness.alternates=lambda:[view,view]
    result=adjudicate('read microscope beside'.split(),source,witness)
    assert result['tokens'][1]=='microscope'
    assert result['word_notes'][1]['status']=='UNRESOLVED'


def test_exact_alternate_pixels_settle_a_spurious_agreeing_reading_conflict():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read every original detail beside the window.')
    witness=pa.CropWitness([dict(w,text='eveiy'if w['text']=='every'else w['text'])for w in source])
    witness.alternates=lambda:[source,source]
    conflicts=pa.audit_agreement([w['text']for w in source],source,witness)
    assert set(conflicts)=={2}
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes=conflicts)
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert result['marked']==set()
    assert result['word_notes']=={}
    assert result['verified_unchanged']==[2]


def test_one_coherent_frame_pair_can_settle_a_number_but_conflicting_pairs_cannot():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read section 4.2.17; before opening the box.')
    bad=words('Please read section 4.2-17; before opening the box.',40)
    witness=pa.CropWitness(bad)
    witness.alternates=lambda:[source,source,bad,bad]
    result=adjudicate([w['text']for w in bad],source,witness)
    assert result['tokens']==[w['text']for w in source]
    assert result['word_notes'][3]['status']=='RESOLVED'
    conflict=words('Please read section 4.2-17; before opening the box.')
    witness.alternates=lambda:[source,source,conflict,conflict]
    result=adjudicate([w['text']for w in bad],source,witness)
    assert result['word_notes'][3]['status']=='UNRESOLVED'


def test_an_owned_english_word_witness_prevents_spurious_image_replacement():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The careful sample remains beside the window.')
    witness=pa.CropWitness(source)
    witness.alternates=lambda:[words('The careful sampie remains beside the window.',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['sample'],model=['sample'],chosen=['sample'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness,allow_clear=False)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['marked']=={2},'A local optical claim remains visibly unverified'


def test_case_only_alternate_noise_keeps_the_owned_literal_word_live():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clear SAMPLE remains beside the window.',40)
    witness=pa.CropWitness(words('The clear sampie remains beside the window.',40))
    witness.alternates=lambda:[words('The clear Sample remains beside the window.',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['SAMPLE'],model=['SAMPLE'],chosen=['SAMPLE'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['marked']=={2}


def test_partial_printed_wrap_is_not_counterevidence_to_a_complete_word():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('across the mea- while waiting',40)
    witness=pa.CropWitness(source);witness.alternates=lambda:[words('across the mea- while waiting')]*2
    result=dict(tokens='across the meadow while waiting'.split(),marked={2},word_notes={2:dict(source=['mea-'],model=['meadow'],chosen=['meadow'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['tokens'][2]=='meadow'


def test_marker_suffix_noise_cannot_replace_its_unchanged_lexical_prefix():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clear crystal(2048), remains visible.',40)
    witness=pa.CropWitness(words('The clear crystal(**), remains visible.',40))
    witness.alternates=lambda:[words('The clear crystal(°°), remains visible.',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['crystal(2048),'],model=['crystal(2048),'],chosen=['crystal(2048),'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['tokens'][2]=='crystal(2048),'


def test_unplaced_edit_cannot_guess_one_word_image_from_an_alignment_box():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Historical examples remain beside the window.',40)
    witness=pa.CropWitness(words('Histrical examples remain beside the window.',40))
    witness.alternates=lambda:[words('Histoical examples remain beside the window.',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={0},word_notes={0:dict(source=[],model=[],chosen=['Historical'],status='UNRESOLVED',mapping='unplaced_within_span')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][0].get('source_image_only')
    assert result['word_notes'][0]['mapping']=='unplaced_within_span'


def test_a_printed_wrap_keeps_its_literal_hyphen_after_lexical_pixel_agreement():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The old bridge‐',40)
    witness=pa.CropWitness(words('The old bridge',0))
    witness.alternates=lambda:[words('The old bridgeE',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['damaged-source-token'],model=['bridge‐'],chosen=['bridge‐'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['tokens'][2]=='bridge‐'


@pytest.mark.parametrize('confidence',[40,99])
def test_alternate_noise_cannot_override_a_confident_original_occurrence(confidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('A printed note remains beside the window.',confidence)
    witness=pa.CropWitness(words('A printed nate remains beside the window.'))
    witness.alternates=lambda:[words('A printed nate remains beside the window.',40)]*2
    witness.source_occurrences=source
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['note'],model=['note'],chosen=['note'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert bool(result['word_notes'][2].get('source_image_only'))is(confidence<85)
    assert result['marked']=={2},'Conflicting local reads remain unresolved'


def test_unsettled_identifier_keeps_its_literal_instance_and_source_control():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please inspect Item12 beside the window.',40)
    witness=pa.CropWitness(words('Please inspect lteml2 beside the window.',40))
    witness.alternates=lambda:[words('Please inspect Iteml2 beside the window.',40)]*2
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:dict(source=['Item12'],model=['Item12'],chosen=['Item12'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][2].get('source_image_only')
    assert result['tokens'][2]=='Item12' and result['marked']=={2}


def test_unavailable_alternate_data_does_not_widen_the_primary_reading_failure(monkeypatch,tmp_path):
    from cps.services.reflow import pixel_adjudication as pa
    primary=words('The independently read sample remains beside the window.')
    monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:primary)
    def unavailable(engine):raise OSError('synthetic unavailable optical data')
    monkeypatch.setattr(pa,'_latin_directory',unavailable)
    import shutil
    monkeypatch.setattr(shutil,'which',lambda name:'/synthetic/tesseract')
    witness=pa.read_crop(None,[[20,20,200,32]],tmp_path)
    assert witness.alternates()==[]
    assert list(witness)==primary


@pytest.mark.parametrize('displaced',[False,True])
def test_unsettled_extra_pixel_ink_is_not_lost_between_fallback_words(displaced):
    """Authored occurrence lists put one ambiguous upright stroke in the gap."""
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Workers carried a tray outside today.')
    glyph=dict(text='|',confidence=55,bbox=[18.5,22,19.5,31])
    if displaced:glyph['bbox']=[118.5,62,119.5,71]
    witness=pa.CropWitness([source[0],glyph,*source[1:]])
    witness.alternates=lambda:[source,source]
    result=pa.refine_fallback([w['text']for w in source],witness,
        {'workers','carried','a','tray','outside','today'},source=source)
    assert result['tokens']==[w['text']for w in source], 'An ambiguous stroke cannot become a guessed live letter'
    notes=result['word_notes']
    if displaced:
        assert not any(n.get('source_image_only')for n in notes.values())
    else:
        assert notes[1]['source_image_only'], 'Unsettled additional ink must remain in the reading'
        assert notes[1]['source_bbox']==[18.5,20,38,32]
        assert notes[1]['status']=='UNRESOLVED'
        assert 1 in result['marked']
        pa.disclose_alternate_conflicts(result,result['boxes'],witness)
        assert notes[1]['source_bbox']==[18.5,20,38,32], 'An alternate supporting the adjacent word cannot erase the uncertain gap'


def test_fallback_extra_ink_survives_the_real_publication_as_its_own_image(tmp_path,monkeypatch):
    """A generated scan prints a narrow letter that both initial inputs omit."""
    from tests.unit import test_round4_defects as ink
    from cps.services.reflow import pixel_adjudication as pa
    page=ink.InkPage();printed='Before lunch I carried a tray outside today.'
    page.text(printed,'body',model=printed.replace(' I ',' ')+
        ' Green baskets contain ripe apples while purple carts carry fresh bread beside a clean window.')
    original=page.finish
    source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.page.get_text('words')]
    def missing_letter():
        image,optical,answer=original()
        return image,[w for w in optical if w['text']!='I'],answer
    page.finish=missing_letter
    witness=pa.CropWitness([dict(w,text='|',confidence=55)if w['text']=='I'else w for w in source])
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    path,report,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    record=next(b for b in report['pages'][0]['blocks']if b['id']=='body')
    assert record['fallback']
    assert record['source_image_words']==[2], 'The ambiguous extra letter and its next word must survive as ink'
    text=' '.join(ink.body_texts(docs))
    assert 'Before lunch'in text and 'a tray outside today.'in text
    assert 'carried'not in text, 'The image contains that word once; do not repeat it as live text'
    appended={id(n)for root in docs.values()for flag in root.iter()
              if flag.get('class')=='reflow-flagged-reading'for n in flag.iter()}
    images=[n for root in docs.values()for n in root.iter()
            if n.get('class')=='source-word-image'and id(n)not in appended]
    assert len(images)==1 and len(images[0])==1
    decision=next(d for d in record['adjudication']if d.get('method')=='unsettled_additional_pixel_ink')
    box=decision['source_bbox']
    for word in source[2:4]:
        x=(word['bbox'][0]+word['bbox'][2])/2;y=(word['bbox'][1]+word['bbox'][3])/2
        assert box[0]<=x<=box[2]and box[1]<=y<=box[3], 'The image owns the omitted stroke and following word'


def test_native_pixel_abort_discloses_only_its_unit_and_keeps_siblings_and_later_pages(tmp_path,monkeypatch):
    # Exercise the damaged-layer audit independently of visible-font proof.
    from cps.services.reflow import native_text
    monkeypatch.setattr(native_text,'drawn_word_authority',lambda *args:False)
    monkeypatch.setattr(native_text,'drawn_word_authorities',lambda page,words:[False]*len(words))
    import pymupdf,zipfile
    from types import SimpleNamespace
    from xml.etree import ElementTree as E
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from tests.unit.test_round4_defects import plain
    first='Quiet readers compare the first printed sample beside the window.'
    sibling='Careful workers keep the second printed sample beside the doorway.'
    later='The next page retains its complete independently printed sentence.'
    failures=[]
    with pymupdf.open()as doc:
        page=doc.new_page(width=430,height=500)
        page.insert_text((30,70),first,fontsize=10)
        page.insert_text((30,180),sibling,fontsize=10)
        page=doc.new_page(width=430,height=500)
        page.insert_text((30,70),later,fontsize=10)
        physical={n:[dict(text=w[4],bbox=list(w[:4]),confidence=99)
                     for w in doc[n].get_text('words')]for n in range(2)}
        def read(display,regions,cache):
            if display.pno==0 and max(box[3]for box in regions)<120:
                failures.append(list(regions))
                raise ValueError('Synthetic native crop recognition failure')
            return physical[display.pno]
        monkeypatch.setattr(pa,'read_crop',read)
        output=tmp_path/'native-scope.epub'
        report=vp.convert(doc,str(output),SimpleNamespace(cache=tmp_path/'cache/readings',key=None))
    with zipfile.ZipFile(output)as archive:
        text=' '.join(plain(E.fromstring(archive.read(n)))for n in archive.namelist()if n.endswith('.xhtml'))
    assert sibling in text and later in text, 'A failed native unit must not consume healthy printed siblings'
    assert first not in text, 'The failed unit must publish its own source image rather than unaudited live text'
    assert report['pages'][0]['source']==report['pages'][1]['source']=='native'
    assert len(failures)==1, 'Recovery must not rerun pixel recognition on an already blocked native unit'
    units=report['pages'][0]['degraded_units']
    assert len(units)==1 and units[0]['block'] is not None
    assert units[0]['reason']=='Synthetic native crop recognition failure'


@pytest.mark.parametrize('failure', [None, 'weak', 'different', 'distant', 'unavailable'])
def test_resampled_pixels_settle_only_the_same_supported_model_occurrence(failure):
    from cps.services.reflow import pixel_adjudication as pa
    source = words('The claar sample remains readable today.')
    model = 'The clear sample remains readable today.'.split()
    witness = pa.CropWitness(words('The clear sample remains readable today.', 39))
    views = [words('The clear sample remains readable today.', 96),
             words('The clear sample remains readable today.', 95)]
    if failure == 'weak':
        views[1][1]['confidence'] = 84
    elif failure == 'different':
        views[1][1]['text'] = 'claar'
    elif failure == 'distant':
        for word in views[1]:
            word['bbox'] = [v + 200 for v in word['bbox']]
    elif failure == 'unavailable':
        views = []
    witness.resampled = lambda: views
    result = adjudicate(model, source, witness)
    assert result['tokens'] == model
    assert result['marked'] == {1}, 'A model correction always retains its source mark.'
    decision = result['word_notes'][1]
    assert decision['status'] == ('RESOLVED' if failure is None else 'UNRESOLVED')
    if failure is None:
        assert decision['source'] == ['claar'] and decision['chosen'] == ['clear']
        assert decision['method'] == 'corroborated_resampled_pixel_readings'


def test_english_resampling_cannot_certify_a_changed_accent():
    from cps.services.reflow import pixel_adjudication as pa
    source = words('Please read cạlm beside the window.')
    witness = pa.CropWitness(words('Please read calm beside the window.', 39))
    witness.resampled = lambda: [words('Please read calm beside the window.'),
                                 words('Please read calm beside the window.')]
    result = adjudicate('Please read calm beside the window.'.split(), source, witness)
    assert result['word_notes'][2]['status'] == 'UNRESOLVED'


def test_two_corroborated_frames_can_settle_a_different_weak_primary_spelling():
    from cps.services.reflow import pixel_adjudication as pa
    source = words('The claar sample remains readable today.')
    model = 'The clear sample remains readable today.'.split()
    witness = pa.CropWitness(words('The lear sample remains readable today.', 39))
    witness.resampled = lambda: [words('The clear sample remains readable today.', 96),
                                 words('The clear sample remains readable today.', 95)]
    result = adjudicate(model, source, witness)
    assert result['tokens'] == model
    assert result['marked'] == {1}
    assert result['word_notes'][1]['status'] == 'RESOLVED'
    assert result['word_notes'][1]['method'] == 'corroborated_resampled_pixel_readings'


def test_resampling_cannot_override_a_confident_primary_spelling_conflict():
    from cps.services.reflow import pixel_adjudication as pa
    source = words('The claar sample remains readable today.')
    witness = pa.CropWitness(words('The lear sample remains readable today.', 96))
    witness.resampled = lambda: [words('The clear sample remains readable today.', 96),
                                 words('The clear sample remains readable today.', 95)]
    result = adjudicate('The clear sample remains readable today.'.split(), source, witness)
    assert result['word_notes'][1]['status'] == 'UNRESOLVED'


@pytest.mark.parametrize('transformed',[False,True])
def test_drawn_native_print_wins_over_conflicting_crop_ocr(tmp_path,monkeypatch,transformed):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,vision_pixels
    class Package:
        def disclosure_image(self,*args):pass
    with pymupdf.open() as doc:
        leaf=doc.new_page(width=400,height=500)
        leaf.insert_text((30,70),'This does not change the clear sam-' if transformed else
                         'This does not change the clear sample.',fontsize=10)
        if transformed:leaf.insert_text((30,85),'ple.',fontsize=10)
        original=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        witness=[dict(w,text='mot'if w['text']=='not'else w['text'])for w in original]
        calls=[]
        def recognize(*args):calls.append(True);return witness
        monkeypatch.setattr(pa,'read_crop',recognize)
        monkeypatch.setattr(vision_pixels,'words',lambda *args:witness)
        text='This does not change the clear sample.'
        page=dict(pno=0,body=['<p>'+text+'</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,55,390,90]])]
        vp._audit_native_pixels(doc,page,records,tmp_path,Package(),{})
    assert vp._seam_text(page['body'][0])==text
    assert page['asides']==[]
    assert not records[0].get('marked')
    assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
    assert calls==[], 'Visible mapped print is not a second OCR transcription'


@pytest.mark.parametrize('kind',['hidden','transparent','overpainted','unmapped'])
def test_unreliable_native_layer_does_not_earn_drawn_print_authority(kind):
    import pymupdf
    from cps.services.reflow import native_text
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((30,70),'A clear sample.',fontsize=10,
            render_mode=3 if kind=='hidden'else 0,fill_opacity=0 if kind=='transparent'else 1)
        if kind=='overpainted':
            from PIL import Image
            import io
            stream=io.BytesIO();Image.new('RGB',(300,200),'white').save(stream,format='PNG')
            leaf.insert_image(leaf.rect,stream=stream.getvalue())
        physical=[dict(text=w[4],bbox=list(w[:4]))for w in leaf.get_text('words')]
        if kind=='unmapped':physical[1]['text']='\ufffd'
        assert not native_text.drawn_word_authority(leaf,physical)


def test_one_alignment_disagreement_cannot_mark_every_confident_neighbor(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    printed='The clear sample remains readable beside the quiet window today.'
    physical=words(printed)
    # A weak secondary recognizer cannot establish uncertainty in agreed words.
    witness=words(printed,30)
    monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        doc.new_page(width=400,height=300)
        _,record=vp.compile_page({'blocks':[block(printed,bbox=(0,0,1000,1000))]},
            [dict(w,text='x'+str(i))for i,w in enumerate(physical)],
            SourceDisplay(doc,0),0,Package(),{},pixel_words=physical,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert record['blocks'][0]['marked']==[], 'No competing word or reader uncertainty exists'


def test_cropped_neighbor_failure_does_not_mark_an_exact_owned_word():
    # Context OCR loses the following short word, but this occurrence agrees.
    source=words('The clear sample remains readable today.')
    witness=[dict(w,confidence=96)for w in source if w['text']!='readable']
    result=adjudicate([w['text']for w in source],source,witness,uncertain=())
    assert not result['marked']
    uncertain=adjudicate([w['text']for w in source],source,witness,uncertain={3})
    assert 3 not in uncertain['marked'], 'Own exact crop proof is independent of its missing neighbor'
    assert 3 in uncertain['verified_unchanged']


@pytest.mark.parametrize(('printed','ocr'),[('each','cach'),('does','docs')])
def test_agreed_dictionary_word_is_not_rewritten_by_duplicate_ocr_settings(printed,ocr):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The reader '+printed+' compares a carefully printed sample.')
    pixels=[dict(w,text=ocr if w['text']==printed else w['text'])for w in source]
    result=pa.adjudicate([w['text']for w in source],source,pixels,corroboration=pixels)
    assert result['tokens']==[w['text']for w in source]
    # A credible competing reading may still receive its own unresolved control.
    notes=pa.audit_agreement(result['tokens'],source,pixels)
    assert notes[2]['alternative']==[ocr]
    assert notes[2]['status']=='UNRESOLVED'


def test_visible_native_words_remain_authoritative_in_a_vision_layout_unit(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_text((30,70),'The clear sample remains readable today.',fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        optical=[dict(w,text='readab1e'if w['text']=='readable'else w['text'])for w in source]
        monkeypatch.setattr(pa,'read_crop',lambda *args:optical)
        result,record=vp.compile_page({'blocks':[block('The clear sample readable today.',bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=optical,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert record['blocks'][0]['published_tokens']=='The clear sample remains readable today.'.split()
    assert all(d['status']=='RESOLVED'for d in record['blocks'][0]['adjudication'])
    assert record['blocks'][0]['marked']==[3], 'Only the real reader deletion earns a correction'


@pytest.mark.parametrize('rotation',[90,180,270])
def test_visible_native_print_authority_uses_unrotated_glyph_coordinates(tmp_path,monkeypatch,rotation):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=300)
        leaf.insert_text((30,70),'The clear sample remains readable today.',fontsize=10)
        leaf.set_rotation(rotation)
        display=SourceDisplay(doc,0)
        box=list(display.reading_rect([25,55,390,80]))
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Native paint must remain authoritative'))
        page=dict(pno=0,body=['<p>The clear sample remains readable today.</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[box])]
        vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
    assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
    assert page['asides']==[]


def test_unknown_legacy_face_cannot_make_a_latin_character_code_authoritative():
    import pymupdf
    from cps.services.reflow import native_text
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=200)
        page.insert_text((30,70),'w',fontname='cour',fontsize=12)
        xref=page.get_fonts()[0][0]
        doc.xref_set_key(xref,'BaseFont','/CustomMarks')
        page=doc.reload_page(page)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
        assert words[0]['text']=='w'
        assert not native_text.drawn_word_authority(page,words)


@pytest.mark.parametrize('recognition_available',[True,False])
@pytest.mark.parametrize('joined_wrap',[False,True])
def test_unproved_legacy_glyph_cannot_rewrite_reliable_neighbor_words(tmp_path,monkeypatch,recognition_available,joined_wrap):
    import pymupdf,re
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,vision_pixels
    class Package:
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((30,70),'not',fontname='helv',fontsize=12)
        leaf.insert_text((70,70),'w',fontname='cour',fontsize=12)
        if joined_wrap:leaf.insert_text((30,100),'sam-\nple',fontname='helv',fontsize=12)
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks')
        leaf=doc.reload_page(leaf)
        original=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        witness=[dict(w,text='mot'if w['text']=='not'else ')')for w in original]
        if not recognition_available:witness=[]
        monkeypatch.setattr(pa,'read_crop',lambda *args:witness)
        monkeypatch.setattr(vision_pixels,'words',lambda *args:witness)
        page=dict(pno=0,body=['<p>not w'+(' sample'if joined_wrap else'')+'</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,55,120,140]])]
        vp._audit_native_pixels(doc,page,records,tmp_path,Package(),{})
    assert vp._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',page['body'][0]))==('not sample'if joined_wrap else'not')
    assert 'class="source-word-image"'in page['body'][0]
    assert records[0]['marked']==[1]
    assert len(page['asides'])==1


@pytest.mark.parametrize('recognition_available',[True,False])
def test_vision_layout_keeps_native_authority_local_to_each_font_occurrence(tmp_path,monkeypatch,recognition_available):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((30,70),'not',fontname='helv',fontsize=12)
        leaf.insert_text((70,70),'w',fontname='cour',fontsize=12)
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');leaf=doc.reload_page(leaf)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        pixels=[dict(w,text='mot'if w['text']=='not'else ')')for w in source]
        if not recognition_available:pixels=[]
        monkeypatch.setattr(pa,'read_crop',lambda *args:pixels)
        _,record=vp.compile_page({'blocks':[block('not w',bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=pixels,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert record['blocks'][0]['published_tokens'][0]=='not'
    assert record['blocks'][0]['marked']==[1]
    assert record['blocks'][0]['source_image_words']==[1]


def test_abbreviated_font_trace_requires_every_matching_resource_to_be_authoritative():
    from cps.services.reflow import native_text
    face='StandardTextFace-Bold'
    assert native_text._face_authority({face+'Extended':True},face)
    assert not native_text._face_authority({face+'Extended':True,face+'Other':False},face)
    assert not native_text._face_authority({'ShortName':True},'Short')


@pytest.mark.parametrize('coverage',[1,.5])
def test_partial_image_overpaint_cannot_borrow_an_uncovered_neighbor_trace(coverage):
    import io,pymupdf
    from PIL import Image
    from cps.services.reflow import native_text
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=200)
        page.insert_text((30,70),'clear sample',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
        picture=io.BytesIO();Image.new('RGB',(2,2),'white').save(picture,format='PNG')
        box=pymupdf.Rect(words[1]['bbox']);box.x1=box.x0+box.width*coverage
        page.insert_image(box,stream=picture.getvalue())
        assert native_text.drawn_word_authorities(page,words)==[True,False]


def test_unmapped_native_word_ink_keeps_its_separately_proved_note_action(tmp_path,monkeypatch):
    import pymupdf,re
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    class Package:
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((30,70),'not',fontsize=12)
        leaf.insert_text((70,70),'w1',fontname='cour',fontsize=12)
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');leaf=doc.reload_page(leaf)
        label=next(c[3]for t in leaf.get_texttrace()for c in t['chars']if c[0]==ord('1'))
        page=dict(pno=0,body=['<p>not w<a id="origin" class="noteref" href="#note"><sup>1</sup></a></p>'],asides=[])
        images={}
        vp._audit_native_pixels(doc,page,[dict(id='native-0',role='body',source_regions=[[25,55,110,85]])],tmp_path,Package(),images)
    body=page['body'][0]
    assert body.count('id="origin"')==1
    assert body.count('href="#note"')==1
    assert body.count('class="source-word-image"')==1
    assert '<sup>1</sup>'in body
    assert next(iter(images.values()))['bbox'][2]<=label[0]+1e-3
    assert vp._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',body))=='not 1'


def test_mapped_embedded_font_aliases_do_not_lose_native_character_authority():
    import pymupdf
    from cps.services.reflow import native_text
    assert native_text._face_authority({'Embedded Text Face Regular':True},'EmbeddedTextFace-Regular')
    assert not native_text._face_authority({'Embedded Text Face Regular':True,'EmbeddedTextFace-Regular':False},'Embedded Text Face Regular')
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=200)
        page.insert_font(fontname='embedded',fontbuffer=pymupdf.Font('helv').buffer)
        page.insert_text((30,60),'clear sample',fontname='embedded')
        words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
        assert native_text.drawn_word_authority(page,words)


@pytest.mark.parametrize('legacy_initial',[False,True])
def test_native_decorative_initial_cannot_certify_a_truncated_word(tmp_path,monkeypatch,legacy_initial):
    import pymupdf,io
    from PIL import Image
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
        def disclosure_image(self,*args):pass
    package=Package();images={}
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=300)
        face='cour'if legacy_initial else'helv'
        page.insert_text((30,110),'B',fontname=face,fontsize=36)
        if legacy_initial:
            xref=next(f[0]for f in page.get_fonts()if f[3]=='Courier')
            doc.xref_set_key(xref,'BaseFont','/CustomMarks');page=doc.reload_page(page)
        cap=page.get_text('words')[0]
        x=cap[2]+.4;y=cap[1]+12*1.075
        page.insert_text((x,y),'efore the quiet sample remains readable.',fontsize=12)
        page.insert_text((x,y+20),'The following row stays unchanged.',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        body,record=vp.compile_page({'blocks':[block('Before the quiet sample remains readable. The following row stays unchanged.',bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,package,images,pixel_words=[],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        own=record['blocks'][0]
        assert own['published_tokens']=='Before the quiet sample remains readable. The following row stays unchanged.'.split()
        assert own['marked']==([0]if legacy_initial else[])
        assert own['source_image_words']==([0]if legacy_initial else[])
        if legacy_initial:
            name=next(k for k in package.images if 'v4-d-'in k)
            picture=Image.open(io.BytesIO(package.images[name])).convert('L');clip=images[name]['bbox']
            # The compound original word must not reproduce the next row's ink.
            region=(int((x+3-clip[0])*picture.width/(clip[2]-clip[0])),
                    int((cap[1]+26-clip[1])*picture.height/(clip[3]-clip[1])),
                    int((x+20-clip[0])*picture.width/(clip[2]-clip[0])),
                    int((cap[1]+33-clip[1])*picture.height/(clip[3]-clip[1])))
            assert picture.crop(region).getextrema()[0]>235


def test_native_raised_ordinal_is_one_printed_word_without_a_correction(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=200)
        page.insert_text((30,70),'1',fontsize=12)
        digit=page.get_text('words')[0]
        page.insert_text((digit[2]+.1,66),'st',fontsize=7)
        page.insert_text((55,70),'clear sample',fontsize=12)
        suffix=[c[3]for t in page.get_texttrace()if t['size']==7 for c in t['chars']]
        suffix_box=[min(b[0]for b in suffix),min(b[1]for b in suffix),max(b[2]for b in suffix),max(b[3]for b in suffix)]
        source=[dict(text='1',bbox=list(digit[:4]),confidence=99),dict(text='st',bbox=suffix_box,confidence=99)]+[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')if w[4]!='1st']
        _,record=vp.compile_page({'blocks':[block('1st clear sample',bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=[],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert record['blocks'][0]['published_tokens']==['1st','clear','sample']
    assert record['blocks'][0]['marked']==[]


@pytest.mark.parametrize('marker_size',[7,10])
def test_native_fused_raised_reference_cannot_become_a_second_body_number(tmp_path,monkeypatch,marker_size):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    monkeypatch.setattr(vp,'_literal_note_refs',lambda *args,**kwargs:{})
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_text((30,70),'A clear sample',fontsize=12)
        physical=page.get_text('words');last=physical[-1]
        page.insert_text((last[2]+.1,65),'7',fontsize=marker_size)
        label=next(c[3]for t in page.get_texttrace()if t['size']==marker_size for c in t['chars'])
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in physical[:-1]]
        source.append(dict(text='sample7',bbox=[last[0],min(last[1],label[1]),label[2],max(last[3],label[3])],confidence=99))
        page.insert_text((30,160),'7 Words below remain readable.',fontsize=12)
        source.extend(dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')if w[1]>120)
        from cps.services.reflow.native_text import lexical_word_view
        assert lexical_word_view(page,source)==source, 'Raised digits stay literal without a requested note slot'
        def proof(tokens,*args,**kwargs):
            if '<n:n1>'not in tokens:return {}
            return {tokens.index('<n:n1>'):dict(status='UNPROVED',source_bbox=None,
                reason='synthetic_requested_slot_ambiguous',anchor_before_bbox=list(last[:4]),anchor_after_bbox=None)}
        monkeypatch.setattr(vp,'_placeholder_display_evidence',proof)
        context=dict(cache=tmp_path/'cache',labels={'7':[(0,'n1')]},owners={},
            catalogue={(0,'n1'):{'target':'v4-note-0-n1','label':'7'}},refs={})
        _,record=vp.compile_page({'blocks':[block('A clear sample<n:n1>',bbox=(0,0,1000,330)),block('Words below remain readable.',ident='n1',role='footnote',marker='7',bbox=(0,400,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=[],note_context=context)
    assert record['blocks'][0]['published_tokens']==['A','clear','sample','<n:n1>']
    assert record['blocks'][0]['marked']==[]
    assert len(record['note_marker_disclosures'])==1


def test_native_raised_reference_serialization_cannot_discard_its_paragraph(tmp_path):
    import pymupdf
    from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=200)
        leaf.insert_text((30,70),'Ordinary sample,',fontsize=12)
        last=leaf.get_text('words')[-1]
        leaf.insert_text((last[2]+8,70),'remains readable.',fontsize=12)
        leaf.insert_text((last[2]+.1,65),'7',fontsize=7)
        # The renderer paints the reference after the body text in PDF object
        # order, while its physical origin remains inside the first line.
        runs=[('t','Ordinary sample,'),('sup',7),('t',' remains readable.')]
        element=assemble.Element('p',runs=runs,pno=0,bbox=[30,55,300,75])
        book=assemble.Book(elements=[element],pages={0:[element]})
        fragment=build_epub.page_fragment(book,0)
        native=dict(pno=0,body=[fragment],asides=[])
        records=vp._native_seam_blocks(doc,book,0,native)
        assert records[0]['native_occurrence_count']==1
        assert records[0]['source_regions']
        vp._audit_native_pixels(doc,native,records,tmp_path,object(),{})
    assert native['body']==[fragment]
    assert 'Ordinary sample,'in native['body'][0]
    assert 'remains readable.'in native['body'][0]
    assert 'noteref-unresolved'in native['body'][0]
    assert not records[0].get('marked')


def test_native_wrapped_symbol_expression_discloses_only_its_own_word(tmp_path,monkeypatch):
    import pymupdf,re
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    class Package:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
        def disclosure_image(self,*args):pass
    package=Package();images={}
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((30,70),'not',fontsize=12)
        leaf.insert_text((70,70),'x',fontname='cour',fontsize=12)
        leaf.insert_text((80,70),'-',fontsize=12)
        leaf.insert_text((30,100),'z.',fontname='cour',fontsize=12)
        leaf.insert_text((60,100),'sample',fontsize=12)
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');leaf=doc.reload_page(leaf)
        native=dict(pno=0,body=['<p>not x-z. sample</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,55,130,110]])]
        vp._audit_native_pixels(doc,native,records,tmp_path,package,images)
    body=native['body'][0]
    assert vp._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',body))=='not sample'
    assert body.count('class="source-word-image"')==1
    assert records[0]['marked']==[1]
    assert records[0]['source_image_words']==[1]
    assert len(native['asides'])==1
    assert len(package.images)==1


@pytest.mark.parametrize('trace_decoding',['codes','replacement'])
@pytest.mark.parametrize('reading',['invented first forms','καλημέρα κόσμε forms'])
def test_unmapped_native_phrase_cannot_discard_its_proved_prose_neighbors(tmp_path,monkeypatch,reading,trace_decoding):
    import pymupdf,re
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    if trace_decoding=='replacement':
        original=pymupdf.Page.get_texttrace
        def replacement_trace(page,*args,**kwargs):
            traces=original(page,*args,**kwargs)
            for trace in traces:
                if trace['font']=='CustomMarks':
                    trace['chars']=[(65533,*char[1:])if char[0]>32 else char for char in trace['chars']]
            return traces
        monkeypatch.setattr(pymupdf.Page,'get_texttrace',replacement_trace)
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=250)
        page.insert_text((30,70),'The quiet',fontsize=12)
        page.insert_text((90,70),'x z',fontname='cour',fontsize=12)
        page.insert_text((130,70),'remains readable.',fontsize=12)
        xref=next(f[0]for f in page.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');page=doc.reload_page(page)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        body,record=vp.compile_page({'blocks':[block('The quiet '+reading+' remains readable.',bbox=(0,0,1000,1000))]},
            source,SourceDisplay(doc,0),0,Package(),{},pixel_words=[],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    own=record['blocks'][0]
    assert own['published_tokens']=='The quiet x z remains readable.'.split()
    assert own['source_image_words']==[2,3]
    assert own['marked']==[2,3]
    html=''.join(body['body'])
    assert html.count('class="source-word-image"')==2
    assert vp._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',html))=='The quiet remains readable.'
    assert not any(word in html for word in reading.split())


@pytest.mark.parametrize('separate_columns',[False,True])
def test_partial_native_row_cannot_move_its_last_word_before_the_row(separate_columns):
    from cps.services.reflow import vision_pipeline as vp
    from tests.unit.test_reflow_vision_pilot import block
    import pymupdf
    class Display:
        rect=pymupdf.Rect(0,0,300,200)
    first=dict(text='Opening',bbox=[10,10,60,20])
    final=dict(text='ending',bbox=[92,29,130,41])
    lower=[dict(text='The',bbox=[10,32,30,42]),dict(text='clear',bbox=[35,32,65,42])]
    territories=[[0,0,140,32],[0,31,85,45]]
    if separate_columns:
        lower=[dict(w,bbox=[w['bbox'][0]+170,12,w['bbox'][2]+170,22])for w in lower]
        territories[1]=[170,0,260,45]
    b=block('Opening ending The clear'if separate_columns else'Opening The clear ending',bbox=(0,0,1000,1000))
    result=vp._owned_block_words(b,{'blocks':[b]},[first,final,*lower],Display(),{'b1':territories},ordered=True)
    assert [w['text']for w in result]==b['text'].split()


def test_native_print_at_a_partial_row_boundary_keeps_its_order_and_ink_extent(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    printed='The clear sample stays readable. Careful readers keep every word.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=250)
        page.insert_text((30,60),'The clear sample stays readable.',fontsize=12)
        page.insert_text((30,90),'Careful readers keep every word.',fontsize=12)
        raw=page.get_text('words')
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in raw]
        last=source[-1]['bbox']
        territories=[[20,30,350,83],[20,84,last[0]-1,100]]
        body,record=vp.compile_page({'blocks':[block(printed,bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=[],regions={'b1':territories},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    own=record['blocks'][0]
    assert own['published_tokens']==printed.split()
    assert own['marked']==[]
    assert any(b[0]<=last[0] and b[1]<=last[1] and b[2]>=last[2] and b[3]>=last[3]
               for b in own['source_regions']), 'Stored native evidence must contain the actual last word ink'


@pytest.mark.parametrize('wrapped',[True,False])
def test_known_native_wrap_join_is_formatting_not_a_word_correction(tmp_path,monkeypatch,wrapped):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    monkeypatch.setattr(pa,'read_crop',lambda *args:[])
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=250)
        page.insert_text((30,60),'Please read care-',fontsize=12)
        x=30 if wrapped else page.get_text('words')[-1][2]+5
        y=90 if wrapped else 60
        page.insert_text((x,y),'fully.',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        _,record=vp.compile_page({'blocks':[block('Please read carefully.',bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=[],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    own=record['blocks'][0]
    assert own['published_tokens']==('Please read carefully.'.split()if wrapped else'Please read care- fully.'.split())
    if wrapped:assert own['marked']==[], 'Conserved letters joined across a printed line break are not a model rewrite'
    else:assert own['marked'], 'A real same-line space cannot be removed as a line-wrap normalization'


@pytest.mark.parametrize('interleaving_tail',[True,False])
@pytest.mark.parametrize('case',['complete','missing_fragment','wrong_box'])
def test_repeated_native_column_labels_bind_only_complete_factory_occurrences(tmp_path,case,interleaving_tail):
    import pymupdf
    from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
    with pymupdf.open()as doc:
        page=doc.new_page(width=350,height=160)
        columns=[(30,'Clear,','sample'),(120,'Other','label'),(220,'Clear,','sample')]
        if interleaving_tail:columns.append((300,'Later','entry'))
        for x,first,last in columns:
            page.insert_text((x,70),first,fontsize=12)
            page.insert_text((x,85),last,fontsize=12)
        elements=[assemble.Element('p',runs=[('t','Clear, sample')],pno=0,bbox=[25,55,85,90]),
                  assemble.Element('p',runs=[('t','Clear, sample')],pno=0,
                    bbox=[115,55,180,90]if case=='wrong_box'else[215,55,280,90])]
        book=assemble.Book(elements=elements,pages={0:elements})
        native=build_epub._page_blocks({0:build_epub.page_fragment(book,0)})[0]
        if case=='missing_fragment':native['body']=native['body'][:1]
        original=list(native['body']);records=vp._native_seam_blocks(doc,book,0,native)
        # Row-major text interleaves the three columns. Repeated factory text
        # must bind its own proved element, not a global neighboring occurrence.
        if interleaving_tail:assert all(r['native_occurrence_count']==0 for r in records)
        if case=='complete':
            assert [r['source_regions']for r in records]==[[elements[0].bbox],[elements[1].bbox]]
            vp._audit_native_pixels(doc,native,records,tmp_path,object(),{})
            assert native['body']==original
            assert all(r['published_tokens']==['Clear,','sample']for r in records)
            assert all(not r.get('marked')and not r.get('degraded_source_context')for r in records)
        elif case=='missing_fragment':
            assert records[0]['source_regions']==[], 'Unequal factory/fragment counts cannot assign a repeated occurrence'
        else:
            assert records[0]['source_regions']==[elements[0].bbox]
            assert records[1]['source_regions']==[], 'An element box with different printed words cannot earn occurrence proof'


def test_curved_optical_line_instances_keep_complete_printed_row_order():
    from cps.services.reflow.vision_pipeline import _pixel_lines
    rows=[]
    for line, tokens in enumerate([['A', 'careful', 'keeper', 'waits.'], ['Another', 'clear', 'sample', 'follows.']],1):
        for index, token in enumerate(tokens):
            x=20+index*60; y=30+line*15-x*.08
            rows.append(dict(text=token,bbox=[x,y,x+40,y+8],confidence=98,
                             optical_line=[0,1,1,1,line]))
    actual=_pixel_lines(list(reversed(rows)))
    assert [[w['text']for w in row]for row in actual]==[
        ['A','careful','keeper','waits.'],['Another','clear','sample','follows.']]


def test_optical_line_label_cannot_join_geometrically_unrelated_rows():
    from cps.services.reflow.vision_pipeline import _pixel_lines
    source=[dict(text='Upper',bbox=[20,20,60,30],optical_line=[0,1,1,1,1]),
            dict(text='Lower',bbox=[80,200,120,210],optical_line=[0,1,1,1,1])]
    assert [[w['text']for w in row]for row in _pixel_lines(source)]==[['Upper'],['Lower']]


def test_optical_tsv_keeps_observed_line_identity_and_word_confidence(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from cps.services.reflow import vision_pixels
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv=header+'5\t1\t2\t3\t4\t1\t10\t20\t30\t8\t94.5\tClear\n'
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda name:'/synthetic/tesseract')
    monkeypatch.setattr(vision_pixels.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=tsv.encode(),stderr=b''))
    pix=SimpleNamespace(width=100,height=200)
    display=SimpleNamespace(rect=SimpleNamespace(width=100,height=200))
    result=vision_pixels._recognize(display,pix,b'procedural pixels',tmp_path/'fresh.json')
    assert result[0]['text']=='Clear' and result[0]['bbox']==[10,20,40,28]
    assert result[0]['confidence']==94.5
    assert result[0]['optical_line']==[0,1,2,3,4]


def test_pre_metadata_optical_cache_is_preserved_and_replaced_by_a_new_receipt(tmp_path,monkeypatch):
    import hashlib,json
    from types import SimpleNamespace
    from cps.services.reflow import vision_pixels
    image=b'procedural portrait pixels';identity=hashlib.sha256(image).hexdigest()
    old=tmp_path/('literal-tsv-v2-'+identity+'.json')
    before=json.dumps([dict(text='Unproved',bbox=[0,0,10,10])]).encode();old.write_bytes(before)
    pix=SimpleNamespace(width=100,height=200,tobytes=lambda kind:image)
    display=SimpleNamespace(rect=SimpleNamespace(width=100,height=200),pixmap=lambda **k:pix)
    calls=[]
    def recognize(*args):
        calls.append(1);result=[dict(text='Clear',bbox=[10,20,40,28],confidence=94.5,optical_line=[0,1,2,3,4])]
        args[-1].write_text(json.dumps(result));return result
    monkeypatch.setattr(vision_pixels,'_recognize',recognize)
    first=vision_pixels.words(display,tmp_path);second=vision_pixels.words(display,tmp_path)
    assert first==second and first[0]['text']=='Clear' and len(calls)==1
    assert old.read_bytes()==before


@pytest.mark.parametrize('evidence',['complete','wrong_print','unavailable','weak','conflict','other_occurrence',
    'english_disagrees','weak_english','no_alternates','duplicate_occurrence'])
def test_note_prefix_ocr_fragment_needs_own_complete_pixel_read(tmp_path,monkeypatch,evidence):
    import copy
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=220,height=200)
        printed='57'if evidence=='wrong_print'else'357'
        page.insert_text((20,175),printed,fontsize=8)
        page.insert_text((42,175),'Clear note remains readable.',fontsize=8)
        raw=page.get_text('words');marker=list(raw[0][:4])
        raster=page.get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png')
        bitmap=doc.new_page(width=220,height=200)
        bitmap.insert_image(bitmap.rect,stream=raster)
        doc.delete_page(0)
        optical=[dict(text='57'if i==0 else w[4],bbox=list(w[:4]),confidence=96)
                 for i,w in enumerate(raw)]
        block=dict(id='note',role='footnote',marker='357',text='Clear note remains readable.',
            bbox=[50,800,960,920],continues_previous=False,continues_next=False)
        original=copy.deepcopy(block)
        display=SourceDisplay(doc,0)
        scan=dict(display=display,optical=optical,answer=dict(blocks=[block]),
                  regions={'note':[[10,160,210,185]]},pixel_cache=tmp_path/'pixels')
        calls=[]
        def read_crop(actual,regions,cache):
            assert actual is display and regions==[marker] and cache==tmp_path/'pixels'
            calls.append(1)
            if evidence=='unavailable':return pa.CropWitness()
            box=[150,50,165,59]if evidence=='other_occurrence'else marker
            witness=pa.CropWitness([dict(text='358'if evidence=='english_disagrees'else printed,
                bbox=box,confidence=79 if evidence=='weak_english'else 96)])
            if evidence=='duplicate_occurrence':witness.append(dict(witness[0]))
            if evidence=='no_alternates':return witness
            witness.alternates=lambda:[
                [dict(text=printed,bbox=box,confidence=79 if evidence=='weak'else 96)],
                [dict(text='358'if evidence=='conflict'else printed,bbox=box,confidence=96)]]
            return witness
        monkeypatch.setattr(pa,'read_crop',read_crop)
        if evidence=='complete':
            owners,catalogue,labels=vp._note_ownership({0:scan})
            assert owners[(0,'note')]==(0,'note')
            assert catalogue[(0,'note')]['label']=='357'and labels['357']==[(0,'note')]
            assert block['text']==original['text']and block['marker']==original['marker']
            assert len(calls)==1
            proof=scan['source_note_marker_verifications']['note']
            assert proof['label']=='357'and proof['original_readings']==['57']
            assert proof['source_bbox']==marker
        else:
            with pytest.raises(ValueError,match='Selected note marker disagrees'):
                vp._note_ownership({0:scan})
            assert block['text']==original['text']


@pytest.mark.parametrize('selected',['357','457'])
def test_visible_native_note_number_keeps_authority_over_optical_agreement(tmp_path,monkeypatch,selected):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=220,height=200)
        page.insert_text((20,175),'357',fontsize=8)
        page.insert_text((42,175),'Clear note remains readable.',fontsize=8)
        raw=page.get_text('words');marker=list(raw[0][:4])
        optical=[dict(text='57'if i==0 else w[4],bbox=list(w[:4]),confidence=96)
                 for i,w in enumerate(raw)]
        block=dict(id='note',role='footnote',marker=selected,text='Clear note remains readable.',
            bbox=[50,800,960,920],continues_previous=False,continues_next=False)
        scan=dict(display=SourceDisplay(doc,0),optical=optical,answer=dict(blocks=[block]),
                  regions={'note':[[10,160,210,185]]},pixel_cache=tmp_path/'pixels')
        calls=[]
        def read_crop(*args):
            calls.append(1)
            witness=pa.CropWitness([dict(text='457',bbox=marker,confidence=99)])
            witness.alternates=lambda:[[dict(text='457',bbox=marker,confidence=99)]for _ in range(2)]
            return witness
        monkeypatch.setattr(pa,'read_crop',read_crop)
        if selected=='357':
            owners,catalogue,labels=vp._note_ownership({0:scan})
            assert owners[(0,'note')]==(0,'note')
            assert catalogue[(0,'note')]['label']=='357'and labels['357']==[(0,'note')]
            assert scan['source_note_marker_verifications']['note']['method']=='visible_native_note_prefix'
        else:
            with pytest.raises(ValueError,match='Selected note marker disagrees'):
                vp._note_ownership({0:scan})
        assert block['text']=='Clear note remains readable.'
        assert calls==[], 'Optical guesses cannot replace reliable drawn native numerals'


@pytest.mark.parametrize('recognition_available',[True,False])
@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_native_prefix_authority_cannot_certify_outside_legacy_factory_runs(tmp_path,monkeypatch,recognition_available,rotation):
    import re
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,vision_pixels
    class Package:
        def __init__(self):self.calls=[]
        def disclosure_image(self,name,display,box):self.calls.append(list(box))
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=230)
        leaf.insert_text((30,60),'Number',fontname='helv',fontsize=12)
        leaf.insert_text((80,140),'w',fontname='cour',fontsize=12)
        leaf.insert_text((140,160),'x',fontname='cour',fontsize=12)
        font=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(font,'BaseFont','/CustomMarks');leaf.set_rotation(rotation);leaf=doc.reload_page(leaf)
        from cps.services.reflow.source_display import SourceDisplay
        display=SourceDisplay(doc,0)
        physical=[dict(text=w[4],bbox=list(display.reading_rect(w[:4])),confidence=99)for w in leaf.get_text('words')]
        monkeypatch.setattr(pa,'read_crop',lambda *args:physical if recognition_available else [])
        monkeypatch.setattr(vision_pixels,'words',lambda *args:physical if recognition_available else [])
        page=dict(pno=0,body=['<p>Number w x</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[list(display.reading_rect([25,43,76,65]))])]
        package=Package();vp._audit_native_pixels(doc,page,records,tmp_path,package,{})
        visible=vp._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>','',page['body'][0]))
        assert visible=='Number', 'Correct nearby print cannot authorize unbound font codes as words'
        assert page['body'][0].count('class="source-word-image"')==2
        assert records[0]['marked']==[1,2]
        assert len(records[0]['unplaced_native_factory_glyphs'])==2
        assert len(package.calls)==2
        for word,box in zip(physical[1:],package.calls):
            own=word['bbox'];assert box[0]<=own[0]and box[1]<=own[1]and box[2]>=own[2]and box[3]>=own[3]
            assert not pymupdf.Rect(box).intersects(pymupdf.Rect(physical[0]['bbox'])), 'A glyph cannot borrow the known heading'


@pytest.mark.parametrize('failure',['duplicate_source','duplicate_output','missing','hidden'])
def test_unbound_native_factory_glyph_cannot_borrow_another_occurrence(tmp_path,monkeypatch,failure):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=230)
        leaf.insert_text((30,60),'Number',fontname='helv',fontsize=12)
        if failure!='missing':leaf.insert_text((80,140),'w',fontname='cour',fontsize=12,
            render_mode=3 if failure=='hidden'else 0)
        if failure=='duplicate_source':leaf.insert_text((140,160),'w',fontname='cour',fontsize=12)
        for font in leaf.get_fonts():
            if font[3]=='Courier':doc.xref_set_key(font[0],'BaseFont','/CustomMarks')
        leaf=doc.reload_page(leaf)
        monkeypatch.setattr(pa,'read_crop',lambda *args:[])
        page=dict(pno=0,body=['<p>Number '+('w w'if failure=='duplicate_output'else'w')+'</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[25,43,76,65]])]
        with pytest.raises(ValueError,match='Native factory output has no complete printed binding'):
            vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})


@pytest.mark.parametrize('printed,published,allowed',[
    ('A ( q ) remains.','A (<sup>q</sup>) remains.',True),
    ('A part remains.','Apart remains.',False),
    ('There is room.','Thereis room.',False)])
def test_native_factory_punctuation_spacing_cannot_join_printed_words(tmp_path,monkeypatch,printed,published,allowed):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=200)
        leaf.insert_text((25,70),printed,fontsize=12)
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Known drawn words do not need optical spelling guesses'))
        page=dict(pno=0,body=['<p>'+published+'</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[20,50,280,80]])]
        if allowed:
            vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
            assert page['body']==['<p>'+published+'</p>']
            assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
            assert not records[0].get('marked')
        else:
            with pytest.raises(ValueError,match='Native factory output has no complete printed binding'):
                vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})


@pytest.mark.parametrize('reader_error',[False,True])
def test_complete_native_reading_restores_only_its_truncated_optical_territory(tmp_path,monkeypatch,reader_error):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    first='The quiet sample follows a steady path'
    last='and every following word stays readable.'
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        leaf.insert_text((25,70),'w',fontname='cour',fontsize=10)
        leaf.insert_text((40,70),first,fontsize=10)
        leaf.insert_text((25,90),last,fontsize=10)
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');leaf=doc.reload_page(leaf)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        cut=[25,78,42,94];regions={'b1':[[20,55,350,74],cut]}
        short=[w for w in source if any(vp.owned_words([w],b)for b in regions['b1'])]
        def recognize(display,boxes,cache):return [w for w in source if any(vp.owned_words([w],b)for b in boxes)]
        monkeypatch.setattr(pa,'read_crop',recognize)
        chosen=last.replace('readable.','different.')if reader_error else last
        page,report=vp.compile_page({'blocks':[block('• '+first+' '+chosen,bbox=(0,0,1000,1000))]},source,
            SourceDisplay(doc,0),0,Package(),{},pixel_words=short,regions=regions,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=report['blocks'][0]
        assert record['marked']==([0,len(source)-1]if reader_error else[0]), 'Only original glyph ink and an actual reader correction need a mark'
        assert record['source_image_words']==[0], 'Only the unproved font glyph stays original ink'
        assert last in vp._seam_text(page['body'][0])
        assert 'different'not in vp._seam_text(page['body'][0])
        if reader_error:
            assert any(d['status']=='RESOLVED'and d['model']==['different.']and d['chosen']==['readable.']for d in record['adjudication'])
        assert any(g['kind']=='complete_native_reading_territory'for g in record['guard'])
        tail=source[-1]['bbox']
        assert any(b[0]<=tail[0]and b[1]<=tail[1]and b[2]>=tail[2]and b[3]>=tail[3]for b in record['source_regions'])


@pytest.mark.parametrize('failure',['reworded_passage','dropped_word','fused_words','other_body','claimed_instance',
    'hidden_glyph','covered_tail','missing_anchor','dropped_prior','reordered_prior','typed_reference','note_role','short_context'])
def test_complete_native_territory_requires_every_original_occurrence_and_owner(failure):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    first='The quiet sample follows a steady path';last='and every following word stays readable.'
    if failure=='short_context':first='Clear sample';last='remains today.'
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        leaf.insert_text((25,70),'w',fontname='cour',fontsize=10,render_mode=3 if failure=='hidden_glyph'else 0)
        leaf.insert_text((40,70),first,fontsize=10);leaf.insert_text((25,90),last,fontsize=10)
        if failure=='dropped_prior':leaf.insert_text((25,160),'Elsewhere',fontsize=10)
        if failure=='covered_tail':
            import io
            from PIL import Image
            data=io.BytesIO();Image.new('RGB',(1,1),'white').save(data,format='PNG')
            leaf.insert_image(leaf.search_for('readable.')[0],stream=data.getvalue())
        xref=next(f[0]for f in leaf.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(xref,'BaseFont','/CustomMarks');leaf=doc.reload_page(leaf)
        source=[dict(text=w[4],bbox=list(w[:4]))for w in leaf.get_text('words')]
        regions={'b1':[[20,55,350,74],[25,78,42,94]]}
        local=[w for w in source if any(vp.owned_words([w],b)for b in regions['b1'])]
        text='• '+first+' '+last
        if failure=='reworded_passage':text=text.replace('The quiet sample','A noisy paragraph').replace('steady path','distant lane')
        if failure=='short_context':text=text.replace('today.','different.')
        if failure=='dropped_word':text=text.replace('following ','')
        if failure=='fused_words':text=text.replace('every following','everyfollowing')
        if failure=='typed_reference':text=text.replace('sample','sample<n:n1>')
        own=block(text,bbox=(0,0,1000,440));answer={'blocks':[own]};owners={}
        if failure=='other_body':
            other=block(last,ident='b2',bbox=(0,300,1000,440));answer['blocks'].append(other)
            regions['b2']=[[20,78,350,94]];owners['b2']=[20,78,350,94]
        if failure=='claimed_instance':regions['_instances']={vp._source_instance(source[-1]):'another-unit'}
        if failure=='missing_anchor':local=local[:1]
        if failure=='dropped_prior':local.append(source[-1])
        if failure=='reordered_prior':local=list(reversed(local))
        if failure=='note_role':own['role']='footnote';own['marker']='1'
        assert vp._complete_native_reading_territory(own,answer,source,SourceDisplay(doc,0),regions,owners,local)is None


@pytest.mark.parametrize('spelling',['kept_hyphen','joined_wrap','punctuated_ordinal'])
def test_native_complete_print_owns_rows_beyond_a_cached_first_line(tmp_path,monkeypatch,spelling):
    import pymupdf
    from types import SimpleNamespace
    from cps.services.reflow import vision_pipeline as vp,assemble,pixel_adjudication as pa
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        if spelling=='punctuated_ordinal':
            leaf.insert_text((25,70),'The sample (6',fontsize=12)
            box=leaf.get_text('words')[-1][:4]
            leaf.insert_text((box[2],64),'th',fontsize=7)
            leaf.insert_text((box[2]+14,70),'row) remains clear.',fontsize=12)
            published='The sample (6<sup>th</sup> row) remains clear.'
        else:
            leaf.insert_text((25,70),'A quiet sample stays well-',fontsize=12)
            leaf.insert_text((25,90),'formed and readable.',fontsize=12)
            published='A quiet sample stays '+('well-formed'if spelling=='kept_hyphen'else'wellformed')+' and readable.'
        first=[25,54,360,74]
        element=assemble.Element('p',runs=[['t',vp._seam_text(published)]],pno=0,bbox=first)
        book=assemble.Book(pages={0:[element]},elements=[element])
        page=dict(pno=0,body=['<p>'+published+'</p>'],asides=[])
        records=vp._native_seam_blocks(doc,book,0,page)
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Complete mapped print must not be replaced by optical spelling'))
        vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert not records[0].get('marked')
        assert page['body']==['<p>'+published+'</p>']
        if spelling!='punctuated_ordinal':
            assert records[0]['native_geometry_binding']=='unique_complete_source_text'
            assert records[0]['source_regions'][0][3]>90


@pytest.mark.parametrize('case',['plain','italic','leading_spaces','ordinary_size','hidden','repeated','changed_word','foreign_box','semantic_action','other_owner'])
def test_native_detached_prefix_keeps_its_source_position_in_formatted_prose(case):
    import pymupdf,copy
    from cps.services.reflow import vision_pipeline as vp,assemble
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=300)
        def draw(y):
            leaf.insert_text((35,y),'A quiet sample stays',fontsize=12)
            leaf.insert_text((35,y+18),'clear and completely readable.',fontsize=12)
            leaf.insert_text((30,y-4),'1',fontsize=12 if case=='ordinary_size'else 6,
                render_mode=3 if case=='hidden'else 0)
        draw(80)
        if case=='repeated':draw(170)
        first=('   'if case=='leading_spaces'else'')+'A quiet sample stays 1 '
        runs=([['t',first],['t','clear','italic'],['t',' and completely readable.']]
              if case=='italic'else[['t',first+'clear and completely readable.']])
        if case=='changed_word':runs[0][1]=runs[0][1].replace('quiet','noisy')
        if case=='semantic_action':runs=[['t','A quiet sample stays '],['sup','1'],['t','clear and completely readable.']]
        box=[35,65,300,84]if case!='foreign_box'else[35,230,300,250]
        element=assemble.Element('p',runs=runs,pno=0,bbox=box,line_boxes=[tuple(box)])
        elements=[element]
        if case=='other_owner':elements.append(assemble.Element('p',runs=[['t','clear and completely readable.']],pno=0,bbox=[35,84,300,103],line_boxes=[(35,84,300,103)]))
        book=assemble.Book(pages={0:elements},elements=elements)
        before=copy.deepcopy(element.runs)
        receipts=vp._native_publication_ownership(doc,book,[0])[0]
        if case in('plain','italic','leading_spaces'):
            assert element.text.split()==['1','A','quiet','sample','stays','clear','and','completely','readable.']
            assert any(r['kind']=='original_detached_native_prefix'for r in receipts)
            assert element.bbox[3]>98
            if case=='italic':assert ['t','clear','italic']in element.runs
            page=dict(body=['<p>'+vp.build_epub._runs_html(element.runs,set(),{},set())+'</p>'])
            records=vp._native_seam_blocks(doc,book,0,page)
            assert records[0]['native_occurrence_count']==1
        else:
            assert element.runs==before, 'Only a unique visible original prefix may change native run order'
            assert not any(r['kind']=='original_detached_native_prefix'for r in receipts)


@pytest.mark.parametrize('foreign_owner',[False,True])
def test_native_complete_cell_inventory_uses_exact_components_not_its_envelope(tmp_path,monkeypatch,foreign_owner):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,assemble,pixel_adjudication as pa
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        leaf.insert_text((25,70),'Quiet sample',fontsize=12)
        leaf.insert_text((25,90),'stays clear.',fontsize=12)
        leaf.insert_text((240,70),'Other sample',fontsize=12)
        leaf.insert_text((240,90),'remains readable.',fontsize=12)
        leaf.insert_text((155,80),'Separate',fontsize=10)
        text='Quiet sample stays clear. Other sample remains readable.'
        element=assemble.Element('p',runs=[['t',text]],pno=0,bbox=[20,54,360,74])
        elements=[element]
        if foreign_owner:elements.append(assemble.Element('p',runs=[['t','Other sample remains readable.']],pno=0,bbox=[235,54,370,95]))
        book=assemble.Book(pages={0:elements},elements=elements)
        page=dict(pno=0,body=['<p>'+text+'</p>'],asides=[])
        records=vp._native_seam_blocks(doc,book,0,page)
        if foreign_owner:
            assert records[0]['native_geometry_binding']!='unique_complete_source_text'
            return
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Complete mapped original cells do not need optical spelling'))
        vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert records[0]['native_geometry_binding']=='unique_complete_source_text'
        words=[dict(text=w[4],bbox=list(w[:4]))for w in leaf.get_text('words')]
        selected=[w['text']for w in words if any(vp.owned_words([w],b)for b in records[0]['source_regions'])]
        assert selected==text.split()
        assert 'Separate'not in selected, 'An interior foreign cell cannot be borrowed through a rectangular envelope'
        assert page['body']==['<p>'+text+'</p>'] and not records[0].get('marked')


@pytest.mark.parametrize('case',['unique','repeated','contradicted'])
def test_native_single_word_uses_its_complete_original_occurrence(tmp_path,monkeypatch,case):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,assemble,pixel_adjudication as pa
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=250)
        leaf.insert_text((25,50),'Other header',fontsize=12)
        leaf.insert_text((25,120),'Clear.'if case!='contradicted'else'Quiet.',fontsize=12)
        if case=='repeated':leaf.insert_text((25,180),'Clear.',fontsize=12)
        element=assemble.Element('p',runs=[['t','Clear.']],pno=0,bbox=[20,34,200,55])
        book=assemble.Book(pages={0:[element]},elements=[element]);page=dict(pno=0,body=['<p>Clear.</p>'],asides=[])
        record=vp._native_seam_blocks(doc,book,0,page)[0]
        if case=='unique':
            monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('An actual mapped native word requires no optical spelling'))
            vp._audit_native_pixels(doc,page,[record],tmp_path,object(),{})
            assert record['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
            assert record['native_occurrence_count']==1 and record['bbox'][1]>90
            assert page['body']==['<p>Clear.</p>']and not record.get('marked')
        else:
            assert record.get('native_geometry_binding')!='unique_complete_source_text'
            assert record['native_occurrence_count']==(2 if case=='repeated'else 0)


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_native_note_keeps_complete_live_rows_beyond_a_nonempty_cached_box(tmp_path,monkeypatch,rotation):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,assemble,build_epub
    first='The quiet sample remains clear'
    last='and every following printed word stays readable.'
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        leaf.insert_text((35.5,61),'1',fontsize=6)
        leaf.insert_text((40,70),first,fontsize=10)
        leaf.insert_text((40,90),last,fontsize=10)
        leaf.set_rotation(rotation)
        note=assemble.Note(num=1,text=first+' '+last,pno=0,bbox=[20,55,350,74])
        book=assemble.Book(notes=[note],pages={0:[]})
        aside=build_epub._scope_ids(build_epub._aside_html(note,{'1':'ref'},{'1'}),0)
        page=dict(pno=0,body=[],asides=[aside])
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('All actual mapped native note words are already printed'))
        records=vp._audit_native_note_pixels(doc,book,page,tmp_path,None,{})
        assert len(records)==1 and records[0]['published_tokens']==note.text.split()
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert not records[0].get('marked')
        assert last in vp._seam_text(page['asides'][0])
        assert '<a class="note-return" href="#ref" aria-label="Return to note reference">1</a>'in page['asides'][0]
        assert max(box[3]for box in records[0]['source_regions'])>90


@pytest.mark.parametrize('case',['complete','hidden','changed_word','other_owner','semantic_action'])
def test_native_stacked_fraction_repairs_only_its_original_components(tmp_path,monkeypatch,case):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,assemble
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        first='Each quiet sample uses '
        leaf.insert_text((30,100),first,fontsize=12,fontname='tiro')
        x=30+pymupdf.get_text_length(first,fontname='tiro',fontsize=12)
        leaf.insert_text((x,93),'1',fontsize=8,fontname='tiro',render_mode=3 if case=='hidden'else 0)
        leaf.insert_text((x+4,100),'/',fontsize=12,fontname='tiro')
        leaf.insert_text((x+8,104),'n',fontsize=8,fontname='tiro')
        leaf.insert_text((x+15,100),' of the printed line.',fontsize=12,fontname='tiro')
        element=assemble.Element('p',pno=0,bbox=[25,80,360,110],
            runs=[['t',first+'n'],['raised','/ of the printed line.'],['t',' 1']])
        if case=='changed_word':element.runs[0][1]=element.runs[0][1].replace('quiet','loud')
        if case=='semantic_action':element.runs[-1][0]='sup'
        elements=[element]
        if case=='other_owner':elements.append(assemble.Element('p',pno=0,bbox=[25,80,360,110],runs=[['t','Other']]))
        original=[list(r)for r in element.runs]
        book=assemble.Book(elements=elements,pages={0:elements})
        vp._native_publication_ownership(doc,book,[0])
        if case!='complete':
            assert element.runs==original,'Incomplete, hidden or foreign source evidence cannot rewrite a paragraph'
            return
        assert element.text.split()==(first+'1/n of the printed line.').split()
        assert not any(r[0]=='raised'for r in element.runs),'A displaced fraction cannot raise the following body line'
        from cps.services.reflow import pixel_adjudication as pa
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Mapped original fraction atoms need no optical spelling'))
        page=dict(pno=0,body=['<p>'+element.text+'</p>'],asides=[])
        records=vp._native_seam_blocks(doc,book,0,page)
        vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert records[0]['published_tokens']==(first+'1/n of the printed line.').split()
        assert not records[0].get('marked')


def test_native_mixed_line_fraction_preserves_baseline_and_emphasis():
    from cps.services.reflow import extract,assemble,native_text
    text='A /   portion'
    body=extract.Span(text,9,'Times',5,(100,100,165,109),origin_y=109,
        char_boxes=tuple((i,i+1,100+i*5,100,105+i*5,109)for i in range(len(text))))
    num=extract.Span('1',6,'Times',4,(107,98.3,110,104.3))
    den=extract.Span('n',6,'Times',4,(115,104.6,118,110.6))
    bold=extract.Span(' stays clear.',9,'Times',21,(170,100,220,109),origin_y=109)
    marker=extract.Span('3',6,'Times',1,(230,98,233,104))
    block=extract.Block(0,(100,98,233,111),[
        extract.Line([den,body,bold],(100,100,220,111)),
        extract.Line([num],num.bbox),extract.Line([marker],marker.bbox)])
    result=native_text.normalize_blocks([block]);line=result[0].lines[0]
    runs=assemble._line_runs(line,0,set(),set(),[],[],preserve_style=True)
    assert '1/n'in assemble.plain_text(runs)
    assert any(r==['t',' stays clear.','bold']for r in runs)
    assert not any(r[0]=='raised'for r in runs)
    assert any(s.text=='3'and s.superscript for b in result for ln in b.lines for s in ln.spans)


@pytest.mark.parametrize('rotation',[0,90,180,270])
@pytest.mark.parametrize('includes_label',[False,True])
def test_native_note_lexical_copy_keeps_its_original_occurrence_when_rotated(tmp_path,monkeypatch,rotation,includes_label):
    import pymupdf,json
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,assemble,build_epub
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=300,height=250);leaf.insert_text((30,70),'1 1',fontsize=10)
        original=[dict(text=w[4],bbox=list(w[:4]))for w in leaf.get_text('words')]
        leaf.set_rotation(rotation);display=SourceDisplay(doc,0)
        note=assemble.Note(num=1,text='1',pno=0,bbox=vp.vision_reading.union([w['bbox']for w in original])if includes_label else original[1]['bbox'])
        fragment=build_epub._scope_ids(build_epub._aside_html(note,{'1':'ref'},{'1'}),0)
        data=dict(pno=0,body=[],asides=[fragment])
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('A mapped lexical copy has its own original ink'))
        records=vp._audit_native_note_pixels(doc,assemble.Book(notes=[note]),data,tmp_path,None,{})
        expected=list(display.reading_rect(original[1]['bbox']))
        assert records[0]['source_regions']==[expected]
        assert records[0]['published_tokens']==['1']and records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert not records[0].get('marked')
        assert data['asides'][0].count('<a class="note-return" href="#ref" aria-label="Return to note reference">1</a>')==1
        from xml.etree import ElementTree
        tree=ElementTree.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+data['asides'][0]+'</root>')
        spans=[node for node in tree.iter()if node.get('data-source-block')=='native-note-0']
        assert len(spans)==1 and ''.join(spans[0].itertext())=='1'
        assert json.loads(spans[0].get('data-source-regions'))==[expected]


@pytest.mark.parametrize('link_scope',['none','incoming_other','outgoing_other','incoming_own','outgoing_own','unresolved_other'])
def test_complete_native_cells_are_not_blocked_by_another_elements_navigation(tmp_path,monkeypatch,link_scope):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,assemble,build_epub,pixel_adjudication as pa
    text='Upper sample continues quietly. Lower example remains readable.'
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=400,height=250)
        leaf.insert_text((25,35),'Outside',fontsize=12)
        leaf.insert_text((25,80),'Upper sample',fontsize=12)
        leaf.insert_text((25,100),'continues quietly.',fontsize=12)
        leaf.insert_text((240,80),'Lower example',fontsize=12)
        leaf.insert_text((240,100),'remains readable.',fontsize=12)
        leaf.insert_text((160,92),'Separate',fontsize=10)
        doc.new_page(width=400,height=250).insert_text((25,35),'Outside',fontsize=12)
        leaf=doc[0]
        table=assemble.Element('p',runs=[['t',text]],pno=0,bbox=[20,64,380,84])
        other=assemble.Element('p',runs=[['t','Outside']],pno=0,bbox=[20,19,150,39])
        link=dict(id='source-target',status='resolved',pno=0,source_element=1,source_item=None,
            source_offset=0,source_extent=7,dest_page=1,dest_element=0,dest_item=None,dest_offset=0)
        if link_scope.startswith('incoming'):
            link.update(pno=1,source_element=0,dest_page=0,dest_element=0 if link_scope.endswith('own')else 1)
        elif link_scope=='outgoing_own':link.update(source_element=0,source_extent=5)
        elif link_scope=='unresolved_other':link['status']='unresolved'
        navigation=[]if link_scope=='none'else[link]
        target=assemble.Element('p',runs=[['t','Outside']],pno=1,bbox=[20,19,150,39])
        book=assemble.Book(pages={0:[table,other],1:[target]},elements=[table,other,target],source_navigation=navigation)
        if navigation:
            source=doc[link['pno']];wanted='Upper'if link_scope=='outgoing_own'else'Outside'
            word=next(w for w in source.get_text('words')if w[4]==wanted)
            source.insert_link(dict(kind=pymupdf.LINK_GOTO,**{'from':pymupdf.Rect(word[:4])},
                page=link['dest_page'],to=pymupdf.Point(25,80 if link_scope=='incoming_own'else 35)))
        active=[n for n in navigation if n['status']=='resolved']
        fragments=['<p>'+build_epub._nav_runs_html(e.runs,
            build_epub._source_nav_marks(active,0,index,None),set(),{},set())+'</p>'
            for index,e in enumerate((table,other))]
        page=dict(pno=0,body=list(fragments),asides=[])
        records=vp._native_seam_blocks(doc,book,0,page)
        if link_scope in('incoming_own','outgoing_own'):
            assert records[0].get('native_geometry_binding')!='unique_complete_source_text'
            return
        assert records[0]['native_geometry_binding']=='unique_complete_source_text'
        assert records[0]['native_occurrence_count']==1
        monkeypatch.setattr(pa,'read_crop',lambda *args:pytest.fail('Complete original native cells require no optical spelling'))
        vp._audit_native_pixels(doc,page,records,tmp_path,object(),{})
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert records[0]['published_tokens']==text.split()and not records[0].get('marked')
        assert page['body']==fragments
        words=[dict(text=w[4],bbox=list(w[:4]))for w in leaf.get_text('words')]
        selected=[w['text']for w in words if any(vp.owned_words([w],box)for box in records[0]['source_regions'])]
        assert selected==text.split()and 'Separate'not in selected
        if link_scope=='outgoing_other':assert page['body'][1].count('<a href="#source-target">Outside</a>')==1
        if link_scope=='incoming_other':assert page['body'][1].count('<span id="source-target"></span>')==1


@pytest.mark.parametrize('conflict',[False,True])
def test_balanced_optical_word_boundaries_do_not_move_neighbour_uncertainty(tmp_path,monkeypatch,conflict):
    """Two authored OCR boundary errors cancel in count, never in ownership."""
    from tests.unit import test_round4_defects as ink
    from cps.services.reflow import pixel_adjudication as pa
    import copy
    printed='These note books remain beside the tall cupboard today.'
    class BoundaryPage(ink.InkPage):
        def finish(self):
            image,source,answer=super().finish()
            self.actual=copy.deepcopy(source)
            fused=dict(source[1],text='notebooks',bbox=ink.vision_pipeline.vision_reading.union([source[1]['bbox'],source[2]['bbox']]))
            cupboard=source[7];a,b,c,d=cupboard['bbox'];middle=(a+c)/2
            split=[dict(cupboard,text='cup',bbox=[a,b,middle,d]),dict(cupboard,text='board',bbox=[middle,b,c,d])]
            optical=[source[0],fused,*source[3:7],*split,source[8]]
            assert len(optical)==len(source)==9
            return image,optical,answer
    page=BoundaryPage();page.text(printed,'body',size=8)
    def read_crop(*args):
        return pa.CropWitness([dict(w,text='small'if conflict and w['text']=='tall'else w['text'])for w in page.actual])
    monkeypatch.setattr(pa,'read_crop',read_crop)
    _,report,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    record=next(b for b in report['pages'][0]['blocks']if b['id']=='body')
    assert ink.body_texts(docs)==[printed]
    assert set(record['marked'])==({1,2,6,7}if conflict else{1,2,7})
    assert record['source_image_words']==[]


def test_reference_projection_keeps_lazy_pixel_witnesses_and_reserves_the_same_ink():
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    source=pa.CropWitness(words('Clear 2 samples stay readable.'))
    before=[dict(w,bbox=list(w['bbox']))for w in source]
    calls=[]
    def views(name):
        calls.append(name)
        return [[dict(w)for w in source],[dict(w)for w in source]]
    source.alternates=lambda:views('alternate')
    source.resampled=lambda:views('resampled')
    source.word_readings=lambda word:views(('word',word['text']))
    source.mark_absence=lambda word:word['text']=='Clear'
    tokens=['Clear','<n:note>','samples','stay','readable.']
    proofs={1:dict(status='PROVED',label='2',source_bbox=list(source[1]['bbox']))}
    lexical,projected=vp._lexical_marker_view(tokens,source,proofs)
    assert lexical==tokens and calls==[],'projection must not dispatch another recognition'
    for method in('alternates','resampled','word_readings'):
        reader=getattr(projected,method,None)
        assert callable(reader),'the independent witness must survive a typed reference'
        result=reader(source[0])if method=='word_readings'else reader()
        assert [[w['text']for w in view]for view in result]==[['Clear','samples','stay','readable.']]*2
    assert projected.mark_absence(source[0])
    assert source==before,'the original occurrence inventory is immutable'
    assert len(calls)==3


def test_reference_projection_preserves_a_recognition_abort_receipt():
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    source=pa.CropWitness();source.failure='Synthetic crop recognition abort'
    _,projected=vp._lexical_marker_view([],source,{})
    assert getattr(projected,'failure',None)==source.failure


@pytest.mark.parametrize('proposed,recognized',[("We're",'Were'),('"Clear',"'Clear"),('Were',"We're")])
def test_quote_omission_by_ocr_cannot_certify_a_changed_printed_word(proposed,recognized):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read '+recognized+' beside the window.')
    witness=pa.CropWitness([dict(w)for w in source])
    witness.alternates=lambda:[[dict(w)for w in source]]*2
    witness.word_readings=lambda word:[[dict(w)for w in source]]*2
    model=('Please read '+proposed+' beside the window.').split()
    result=adjudicate(model,source,witness,corroboration=source)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED','OCR without an apostrophe is not a proof that the print has none'
    assert note['source_image_only'] and note['source_bbox']==source[2]['bbox']
    assert result['marked']=={2}


def test_quote_codepoint_normalization_does_not_invent_word_uncertainty():
    original=words('Please read we’re beside the window.')
    model="Please read we're beside the window.".split()
    result=adjudicate(model,original,original)
    assert result['tokens']==model and result['marked']==set()


def test_false_optical_quote_cannot_hide_independently_agreed_model_letters():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read ‘Clear beside the window.')
    witness=pa.CropWitness(words('Please read Clear beside the window.'))
    witness.word_readings=lambda word:[words('Please read Clear beside the window.')for _ in range(2)]
    result=adjudicate('Please read -Clear beside the window.'.split(),source,witness)
    assert result['tokens']==['Please','read','Clear','beside','the','window.']
    note=result['word_notes'][2]
    assert note['status']=='RESOLVED' and not note.get('source_image_only')
    assert result['marked']=={2}


@pytest.mark.parametrize('independent',['missing','model','source'])
def test_common_word_cannot_be_replaced_by_shared_network_letter_noise(independent):
    from cps.services.reflow import pixel_adjudication as pa
    import pymupdf
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=400,height=100)
        page.insert_text((20,50),'Please read the clear sample beside the window.',fontsize=10)
        assert 'clear'in page.get_text().split()
    # Both controlled optical networks can misread the same printed letter.

    source=words('Please read the clcar sample beside the window.')
    witness=pa.CropWitness([dict(w)for w in source])
    if independent!='missing':
        text='Please read the '+('clear'if independent=='model'else'clcar')+' sample beside the window.'
        witness.word_readings=lambda word:[words(text),words(text)]
    result=adjudicate('Please read the clear sample beside the window.'.split(),source,witness)
    note=result['word_notes'][3]
    assert result['marked']=={3}
    if independent=='source':
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert result['tokens'][3]=='clear','Optical consensus is not a certificate for a changed nonword'
    elif independent=='model':
        assert result['tokens'][3]=='clear'and note['status']=='RESOLVED'
    else:
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')


@pytest.mark.parametrize('own_support',[False,True])
def test_weak_other_network_cannot_hide_a_positive_own_pixel_word(own_support):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clcar sample beside the window.',40)
    witness=pa.CropWitness(words('Please read the '+('clear'if own_support else'clcar')+' sample beside the window.'))
    witness.alternates=lambda:[words('Please read the clcar sample beside the window.',40)]*2
    witness.source_occurrences=source
    result=dict(tokens='Please read the clear sample beside the window.'.split(),marked={3},word_notes={3:dict(source=['clcar'],model=['clear'],chosen=['clear'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert bool(result['word_notes'][3].get('source_image_only'))is(not own_support)
    assert result['marked']=={3},'A genuine lexical conflict retains its local source control'


@pytest.mark.parametrize('failure',['none','same_row','missing_hyphen','weak','missing_tail','other_occurrence'])
def test_complete_independently_read_line_wrap_is_formatting(failure):
    from cps.services.reflow import pixel_adjudication as pa
    import copy
    source=words('Please read care- fully beside the window.')
    source[2]['bbox']=[100,20,130,32]
    for i,w in enumerate(source[3:]):w['bbox']=[i*30,40,i*30+28,52]
    if failure=='same_row':source=words('Please read care- fully beside the window.')
    if failure=='missing_hyphen':source[2]['text']='care'
    before=copy.deepcopy(source);witness=copy.deepcopy(source)
    if failure=='weak':witness[2]['confidence']=30
    if failure=='missing_tail':del witness[3]
    if failure=='other_occurrence':
        for w in witness:w['bbox']=[v+500 for v in w['bbox']]
    result=adjudicate('Please read carefully beside the window.'.split(),source,witness,uncertain={2})
    if failure=='none':
        assert result['tokens']=='Please read carefully beside the window.'.split()
        assert result['marked']==set(),'A proved layout division changes no printed letters'
    else:assert result['marked'],'Unproved divisions cannot normalize away a real word boundary'
    assert source==before


@pytest.mark.parametrize('fragment',[False,True])
def test_a_cropped_glyph_fragment_cannot_certify_a_whole_word_replacement(fragment):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the quirble sample beside the window.')
    optical=words('Please read the genuine sample beside the window.')
    if fragment:optical[3]['bbox']=[63,20,68,32]
    result=pa.adjudicate([w['text']for w in source],source,optical,corroboration=optical)
    assert result['tokens'][3]==('quirble'if fragment else'genuine')
    if fragment:assert 3 not in result['marked']
    else:assert result['word_notes'][3]['status']=='RESOLVED'


@pytest.mark.parametrize('fragment',[False,True])
def test_partial_secondary_ink_cannot_create_a_whole_word_conflict(fragment):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear sample beside the window.')
    optical=words('Please read the clcar sample beside the window.')
    if fragment:optical[3]['bbox']=[63,20,68,32]
    notes=pa.audit_agreement([w['text']for w in source],source,optical)
    assert set(notes)==(set()if fragment else{3})
    if not fragment:assert notes[3]['alternative']==['clcar']


def test_a_partial_secondary_word_cannot_force_a_live_word_into_an_image():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear sample beside the window.',40)
    witness=pa.CropWitness(words('Please read the clcar sample beside the window.'))
    partial=words('Please read the clcar sample beside the window.',40);partial[3]['bbox']=[63,20,68,32]
    witness.alternates=lambda:[partial,partial]
    result=dict(tokens='Please read the clear sample beside the window.'.split(),marked={3},word_notes={3:dict(source=['clcar'],model=['clear'],chosen=['clear'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert not result['word_notes'][3].get('source_image_only')
    assert result['marked']=={3},'The actual full conflicting occurrence still retains its local uncertainty'


@pytest.mark.parametrize('original,second,proposed', [('—','—','–'),('–','–','—'),('–','-','–')])
def test_a_complete_printed_horizontal_dash_has_no_uncertain_lexical_word(original,second,proposed):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clear sample '+original+' remains readable.')
    witness=words('The clear sample '+second+' remains readable.')
    result=adjudicate(('The clear sample '+proposed+' remains readable.').split(),source,witness,uncertain={3})
    assert result['marked']==set()
    assert result['tokens']==('The clear sample '+proposed+' remains readable.').split()
    assert pa.audit_agreement(result['tokens'],source,witness)=={}


@pytest.mark.parametrize('original,proposed',[('clear—sample','clear–sample'),('3—4','3–4'),('−','–'),(',', '.'),('—','minus')])
def test_dash_formatting_cannot_clear_a_lexical_or_different_symbol_edit(original,proposed):
    source=words('Read '+original+' beside the window.')
    result=adjudicate(('Read '+proposed+' beside the window.').split(),source,source)
    assert 1 in result['marked']


@pytest.mark.parametrize('failure',['weak','other_occurrence'])
def test_unproved_horizontal_punctuation_cannot_claim_a_formatting_settlement(failure):
    source=words('The clear sample — remains readable.')
    witness=words('The clear sample — remains readable.',89 if failure=='weak' else 99)
    if failure=='other_occurrence':
        for word in witness:word['bbox']=[v+500 for v in word['bbox']]
    result=adjudicate('The clear sample – remains readable.'.split(),source,witness)
    assert 3 in result['marked']


@pytest.mark.parametrize('independent',['missing','weak','model','source','remote'])
def test_one_recognition_network_cannot_certify_a_conflicting_dictionary_word(independent):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clean sample beside the window.')
    witness=pa.CropWitness([dict(w)for w in source])
    def second(word):
        if independent=='missing':return []
        text='Please read the '+('clear'if independent=='model'else'clean')+' sample beside the window.'
        views=[words(text,40 if independent=='weak'else 99)for _ in range(2)]
        if independent=='remote':
            for view in views:
                for item in view:item['bbox']=[v+500 for v in item['bbox']]
        return views
    witness.word_readings=second
    result=adjudicate('Please read the clear sample beside the window.'.split(),source,witness)
    note=result['word_notes'][3]
    assert result['marked']=={3}
    if independent in('source','model'):
        assert result['tokens'][3]==('clean'if independent=='source'else'clear')
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
    else:
        assert note['status']=='UNRESOLVED'and note.get('source_image_only'), 'A repeated dictionary word from one network is not diverse pixel proof'


@pytest.mark.parametrize('symbol',['□','◇','△','⊕'])
def test_alphabetic_recognizers_cannot_certify_a_mathematical_symbol_as_a_digit(symbol):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the (0) symbol beside the sample.')
    witness=pa.CropWitness([dict(w)for w in source])
    witness.word_readings=lambda word:[[dict(w)for w in source]for _ in range(2)]
    result=adjudicate(('Read the ('+symbol+') symbol beside the sample.').split(),source,witness)
    note=result['word_notes'][2]
    assert result['marked']=={2}
    assert note['status']=='UNRESOLVED'and note.get('source_image_only'), 'An unsupported symbol remains original ink, never a certified numeral'


@pytest.mark.parametrize('independent_agrees',[False,True])
def test_agreeing_baselines_cannot_be_rewritten_by_one_dictionary_network(independent_agrees):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the flarn sample beside the window.')
    optical=words('Please read the learn sample beside the window.')
    witness=pa.CropWitness(optical)
    witness.word_readings=lambda word:[optical, optical if independent_agrees else source]
    result=adjudicate([w['text']for w in source],source,witness,corroboration=optical)
    assert result['tokens'][3]==('learn'if independent_agrees else'flarn')
    if independent_agrees:assert result['word_notes'][3]['status']=='RESOLVED'
    else:assert 3 not in result['marked'], 'The caller audits the original conflicting occurrence; no replacement was proved here'


@pytest.mark.parametrize('suffix',[',','+)','‡'])
def test_unchanged_lexical_word_keeps_its_proved_literal_suffix(suffix):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Please read the clear'+suffix+' sample beside the window.')
    witness=pa.CropWitness([dict(w)for w in source])
    witness.word_readings=lambda word:[]
    result=adjudicate('Please read the clear sample beside the window.'.split(),source,witness)
    assert ''.join(c for c in result['tokens'][3]if c.isalnum())=='clear'
    assert not result.get('word_notes',{}).get(3,{}).get('source_image_only'), 'Punctuation and a separately proved marker do not dispute the unchanged lexical word'


@pytest.mark.parametrize('failure',['none','small_line','weak','remote','network_conflict','frame_conflict'])
def test_complete_own_word_uses_diverse_single_line_proof_when_word_mode_is_weak(monkeypatch,tmp_path,failure):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    import shutil
    source=words('Please read the clean sample beside the window.')
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:set('please read the clean clear sample beside window'.split()))
    calls=[]
    def recognize(display,regions,cache,language='eng',scale=4,tessdata=None,psm=6):
        calls.append((language,scale,psm))
        if psm==6:return [dict(w)for w in source]
        text='clear'if (failure=='network_conflict'and language=='LatinRaw')or(failure=='frame_conflict'and scale==6)else'clean'
        word=dict(source[3],text=text,confidence=40 if psm==8 or failure=='weak'or(failure=='small_line'and scale!=3)else 99)
        if failure=='remote'and psm==7:word['bbox']=[v+500 for v in word['bbox']]
        return [word]
    monkeypatch.setattr(pa,'_read_crop',recognize)
    monkeypatch.setattr(pa,'_latin_directory',lambda engine:tmp_path)
    original_which=shutil.which
    monkeypatch.setattr(shutil,'which',lambda name:'synthetic-recognizer'if name=='tesseract'else original_which(name))
    witness=pa.read_crop(None,[[0,0,200,40]],tmp_path)
    witness.alternates=lambda:[]
    witness.resampled=lambda:[]
    result=adjudicate('Please read the clear sample beside the window.'.split(),source,witness)
    note=result['word_notes'][3]
    if failure in('none','small_line'):
        assert result['tokens'][3]=='clean'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert ('eng',4,7)in calls and ('LatinRaw',4,7)in calls
    else:assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert result['marked']=={3},'Every settled correction retains its own provenance'


@pytest.mark.parametrize('proposed,damaged,tail,prefix', [
    ('example-book','cxample-book','ample-book','cx'),
    ('several','cveral','veral','c'),
    ("Harbor’s","Harbor’",'Harbor','5'),
])
def test_split_optical_fragments_cannot_certify_a_complete_replacement(proposed,damaged,tail,prefix):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+damaged+' sample now.')
    witness=pa.CropWitness(words('Read the '+damaged+' sample now.',40))
    box=source[2]['bbox']
    if prefix=='5':
        fragments=[dict(text=tail,bbox=[40,20,55,32],confidence=99),
                   dict(text=prefix,bbox=[56,23,58,29],confidence=50)]
    else:
        fragments=[dict(text=prefix,bbox=[40,24,44,31],confidence=20),
                   dict(text=tail,bbox=[45,20,58,32],confidence=99)]
    witness.word_readings=lambda word:[fragments,fragments]
    result=pa.adjudicate(('Read the '+proposed+' sample now.').split(),source,witness)
    assert result['tokens'][2]==proposed
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert not result['word_notes'][2].get('source_image_only')


def test_complete_pixel_read_can_corroborate_model_when_other_network_splits_ink():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the cxample-book sample now.',40)
    witness=pa.CropWitness([dict(w)for w in source])
    intact=dict(text='example-book',bbox=source[2]['bbox'],confidence=96)
    fragments=[dict(text='cx',bbox=[40,24,44,31],confidence=20),
               dict(text='ample-book',bbox=[45,20,58,32],confidence=99)]
    witness.word_readings=lambda word:[[intact],fragments]
    result=pa.adjudicate('Read the example-book sample now.'.split(),source,witness)
    assert result['tokens'][2]=='example-book'
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['marked']=={2},'An actual model correction keeps its source-linked record'


def test_a_fused_optical_token_cannot_delete_a_model_word_boundary():
    source=words('Read As a sample now.')
    source[1]['text']='Asa';source[1]['bbox']=[20,20,58,32];del source[2]
    witness=[dict(w)for w in source]
    result=adjudicate('Read As a sample now.'.split(),source,witness)
    assert result['tokens']=='Read As a sample now.'.split()
    assert all(d['status']=='UNRESOLVED'for d in result['decisions'])
    assert result['marked'],'The unproved boundary remains locally disclosed'


@pytest.mark.parametrize('model,damaged',[('uv.','uv,'),('ex','cx')])
@pytest.mark.parametrize('source_confidence',[40,99])
def test_lower_ink_only_cannot_certify_a_complete_changed_word(model,damaged,source_confidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+damaged+' sample now.',source_confidence)
    witness=pa.CropWitness(words('Read the '+model+' sample now.'))
    witness[2]['bbox']=[40,26.5,58,32]
    witness.word_readings=lambda word:[list(witness),list(witness)]
    result=pa.adjudicate(('Read the '+model+' sample now.').split(),source,witness)
    assert result['word_notes'][2]['status']==('UNRESOLVED'if source_confidence<85 else'RESOLVED')
    assert bool(result['word_notes'][2].get('source_image_only'))is(source_confidence<85)
    if source_confidence<85:assert result['word_notes'][2]['source_bbox']==source[2]['bbox']


def test_a_recognized_tail_without_its_prefix_is_not_complete_word_evidence():
    from cps.services.reflow import pixel_adjudication as pa
    source=dict(text='cveral',bbox=[20,20,60,32],confidence=40)
    tail=dict(text='veral',bbox=[28,20,60,32],confidence=99)
    assert pa._paired_word_choice(source,[[tail],[tail]])is None
    assert pa.audit_agreement(['several'],[source],[tail])=={}


def test_a_confident_recognized_tail_cannot_force_a_complete_model_word_into_an_image():
    from cps.services.reflow import pixel_adjudication as pa
    source=dict(text='cveral',bbox=[20,20,60,32],confidence=40)
    tail=dict(text='veral',bbox=[28,20,60,32],confidence=99)
    witness=pa.CropWitness([source]);witness.alternates=lambda:[[tail],[tail]]
    result=dict(tokens=['several'],marked={0},word_notes={0:dict(
        source=['cveral'],model=['several'],chosen=['several'],status='UNRESOLVED')})
    pa.disclose_alternate_conflicts(result,{0:source['bbox']},witness)
    assert not result['word_notes'][0].get('source_image_only')
    assert result['tokens']==['several']and result['marked']=={0}


@pytest.mark.parametrize('optical',['missing','weak','fragment','competing','reader_uncertain','rejected','lower_only'])
def test_complete_vision_word_is_not_disputed_by_missing_or_incomplete_optical_evidence(optical):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    printed='Read the clear sample now.'
    with pymupdf.open()as drawing,pymupdf.open()as scan:
        page=drawing.new_page(width=300,height=100)
        page.insert_text((20,50),printed,fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=40)for w in page.get_text('words')]
        image=page.get_pixmap().tobytes('png')
        scan.new_page(width=300,height=100).insert_image(page.rect,stream=image)
        assert not scan[0].get_text(),'The synthetic source is a scan, with no native-text authority'
    source[2]['text']='clcar'
    witness=pa.CropWitness([]if optical=='missing'else[dict(w)for w in source])
    if optical=='competing':witness[2]['confidence']=99
    if optical=='fragment':
        box=witness[2]['bbox'];witness[2].update(text='car',confidence=99,bbox=[box[0]+.25*(box[2]-box[0]),box[1],box[2],box[3]])
    if optical=='lower_only':
        box=witness[2]['bbox'];witness[2].update(confidence=99,bbox=[box[0],box[1]+.6*(box[3]-box[1]),box[2],box[3]])
    witness.word_readings=lambda word:[]
    witness.model_reading=True
    result=pa.adjudicate(printed.split(),source,witness,
        uncertain={2}if optical=='reader_uncertain'else(),unresolved_source=optical=='rejected')
    note=result['word_notes'][2]
    if optical in('missing','weak','fragment'):
        assert result['tokens'][2]=='clear'
        assert note['status']=='RESOLVED'
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
        assert result['marked']=={2},'Every real model correction still has a source-linked record'
    else:
        assert note['status']=='UNRESOLVED','Real competing ink, explicit reader doubt and rejected readings stay disclosed'


def test_compiled_vision_correction_names_its_actual_model_evidence(tmp_path,monkeypatch):
    from tests.unit import test_round4_defects as ink
    from cps.services.reflow import pixel_adjudication as pa
    page=ink.InkPage();printed='Read the clear sample now.'
    page.text(printed,'body')
    finish=page.finish
    def weak_optical():
        image,optical,answer=finish()
        for word in optical:
            if word['text']=='clear':word.update(text='clcar',confidence=40)
        return image,optical,answer
    page.finish=weak_optical
    def weak_crop(display,regions,cache):
        # This fixture controls recognition only; the original scan still prints
        # the complete first reading. The other letters retain real geometry.
        source=ink.vision_pixels.words(display,cache)
        return pa.CropWitness(source)
    monkeypatch.setattr(pa,'read_crop',weak_crop)
    _,report,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    assert ink.body_texts(docs)==[printed]
    decisions=report['pages'][0]['blocks'][0]['adjudication']
    assert any(d['status']=='RESOLVED'and d['method']=='complete_vision_reading_without_optical_counterevidence'for d in decisions)
    notes=[' '.join(''.join(n.itertext()).split())for root in docs.values()for n in root.iter()
           if n.get('class')=='footnote vision-disclosure']
    assert any('The complete vision reading is retained.'in text for text in notes)
    assert all('upright crop'not in text for text in notes)


def test_failed_secondary_recognition_cannot_clear_explicit_reader_doubt():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the clear sample now.')
    witness=pa.CropWitness()
    witness.failure='Synthetic secondary recognition unavailable'
    result=pa.adjudicate('Read the clear sample now.'.split(),source,witness,uncertain={2})
    assert result['tokens'][2]=='clear'
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['marked']=={2}


@pytest.mark.parametrize('source_text,model_text',[('cafe','café'),('as','As'),('because','(because')])
def test_first_vision_read_does_not_settle_unproved_small_marks_or_case(source_text,model_text):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+source_text+' sample now.',40)
    witness=pa.CropWitness();witness.model_reading=True
    result=pa.adjudicate(('Read the '+model_text+' sample now.').split(),source,witness)
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['marked']=={2}


def test_full_length_counterreading_in_a_padded_source_box_still_disputes_vision():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the clean sample now.',40)
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness[2].update(confidence=99,bbox=[42,20,56,32])
    result=pa.adjudicate('Read the clear sample now.'.split(),source,witness)
    assert result['word_notes'][2]['status']=='UNRESOLVED'
    assert result['marked']=={2}


@pytest.mark.parametrize('confidence',[40,99])
@pytest.mark.parametrize('literal',['continuation','con-tinuation'])
@pytest.mark.parametrize('secondary_failed',[False,True])
def test_identical_strong_source_letters_at_a_printed_line_division_are_formatting(confidence,literal,secondary_failed):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    with pymupdf.open()as page_source:
        page=page_source.new_page(width=240,height=100)
        page.insert_text((130,35),'con-',fontsize=12)
        page.insert_text((20,52),'tinuation',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=confidence)for w in page.get_text('words')]
        assert [w['text']for w in source]==['con-','tinuation']
        witness=pa.CropWitness();witness.model_reading=True
        if secondary_failed:witness.failure='Synthetic recognition failed'
        result=pa.adjudicate([literal],source,witness)
    assert result['tokens']==[literal]
    if confidence==99 and not secondary_failed:
        assert not result['marked'],'No lexical letter changed at this source-proven line division'
        assert not result['word_notes']
    else:assert result['marked'],'Weak original letters still need independent proof'


def test_an_in_row_hyphen_is_not_an_end_of_line_normalization():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('con- tinuation')
    result=pa.adjudicate(['continuation'],source,pa.CropWitness())
    assert result['marked'],'A same-row source division cannot be joined on this rule'


def test_a_first_readers_explicit_doubt_is_not_cleared_by_line_division_formatting():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('con- tinuation')
    source[0]['bbox']=[100,20,130,32];source[1]['bbox']=[0,40,60,52]
    witness=pa.CropWitness();witness.model_reading=True
    result=pa.adjudicate(['continuation'],source,witness,uncertain={0})
    assert result['marked']and result['word_notes'][0]['status']=='UNRESOLVED'


@pytest.mark.parametrize('candidate',['moming','buming'])
def test_agreement_between_word_crops_cannot_bypass_nonword_safety(candidate):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    printed='morning' if candidate=='moming' else 'burning'
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=240,height=100)
        page.insert_text((20,50),'Read the '+printed+' sample now.',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    source[2]['text']=candidate
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(w)for w in source],[dict(w)for w in source]]
    result=pa.adjudicate(('Read the '+printed+' sample now.').split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED','Repeated optical shape errors cannot certify a changed nonword'
    assert note.get('source_image_only'),'The dictionary alone cannot prove either spelling against ink'
    assert result['marked']=={2}


@pytest.mark.parametrize('conflict',['none','other_owner','different_tail'])
def test_cropped_note_box_cannot_drop_a_source_confirmed_following_line(tmp_path,monkeypatch,conflict):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    first='The example data come from a printed reference'
    tail='at noon near the wide open garden gate.'
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        leaf=drawing.new_page(width=400,height=250)
        leaf.insert_text((40,170),first,fontsize=10)
        leaf.insert_text((25,183),tail,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        original=leaf.get_pixmap().tobytes('png')
        doc.new_page(width=400,height=250).insert_image(leaf.rect,stream=original)
        assert not doc[0].get_text(),'The pixels are the fixture; supplied OCR is not native authority'
        own=block(first+' '+(tail if conflict!='different_tail'else tail.replace('noon','dawn')),ident='n1',role='footnote',marker='1',bbox=(90,625,800,690))
        answer={'blocks':[own]}
        if conflict=='other_owner':answer['blocks'].append(block(tail,ident='b2',bbox=(50,690,900,750)))
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness([dict(w)for w in source if any(vp.owned_words([w],b)for b in boxes)]))
        page,report=vp.compile_page(answer,source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=next(b for b in report['blocks']if b['id']=='n1')
        expanded=any(g['kind']=='complete_note_tail_territory'for g in record['guard'])
        if conflict=='none':
            assert record['published_tokens']==(first+' '+tail).split()
            assert expanded
            assert not record['marked']
            end=source[-1]['bbox']
            assert any(b[0]<=end[0]and b[1]<=end[1]and b[2]>=end[2]and b[3]>=end[3]for b in record['source_regions'])
        else:assert not expanded,'Different words or another source owner cannot expand this note'


@pytest.mark.parametrize('third',['sample!','sample?'])
def test_a_quote_like_source_mark_cannot_be_certified_as_invented_third_punctuation(third):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=240,height=100)
        page.insert_text((20,50),'Read this sample',fontsize=12)
        page.insert_text((112,45),'1',fontsize=7)
        page.insert_text((125,50),'now.',fontsize=12)
        assert 'sample1'in page.get_text().split()
    source=words("Read this sample' now.")
    witness=pa.CropWitness(words('Read this '+third+' now.'))
    result=pa.adjudicate('Read this sample now.'.split(),source,witness,corroboration=list(witness))
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert result['tokens'][2]=='sample','Recognition cannot invent punctuation from ambiguous raised ink'
    assert result['marked']=={2}


@pytest.mark.parametrize('counter,latin_confidence,model_confidence,complete',[
    ('clcar',40,94,True),('clcar',99,94,True),
    ('clean',40,94,True),('clcar',40,89,True),('clcar',40,94,False),
])
def test_one_network_nonword_noise_cannot_veto_complete_pixel_support_for_model(
        counter,latin_confidence,model_confidence,complete):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    printed='Read the clear sample now.'
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=300,height=100)
        page.insert_text((20,50),printed,fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    source[2]['text']='clcar'
    primary=pa.CropWitness([dict(w)for w in source]);primary.model_reading=True
    model=dict(source[2],text='clear',confidence=model_confidence)
    if not complete:
        a=model['bbox'];model['bbox']=[a[0]+.3*(a[2]-a[0]),a[1],a[2],a[3]]
    weak=dict(source[2],text=counter,confidence=40)
    contrary=dict(source[2],text=counter,confidence=99)
    latin=dict(contrary,confidence=latin_confidence)
    primary.word_readings=lambda word:[[model],[weak],[contrary],[latin]]
    result=pa.adjudicate(printed.split(),source,primary)
    note=result['word_notes'][2]
    if counter=='clcar'and latin_confidence==40 and model_confidence>=90 and complete:
        assert result['tokens'][2]=='clear'
        assert note['status']=='RESOLVED'
        assert note['method']=='intact_pixel_corroboration_of_model'
        assert not note.get('source_image_only')
    else:
        assert note['status']=='UNRESOLVED'
    assert result['marked']=={2},'The actual model correction retains its source-linked record'


@pytest.mark.parametrize('model,candidate,printed,status',[
    ('barn','bam','barn','UNRESOLVED'),
    ('modern','modem','modern','UNRESOLVED'),
    ('modem','modern','modem','UNRESOLVED'),
    ('clear','clean','clean','RESOLVED'),
])
def test_shared_joined_strokes_cannot_certify_even_a_dictionary_word(
        monkeypatch,model,candidate,printed,status):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(
        {'read','the','sample','now',model,candidate}))
    assert not pa._dictionary_regression(model,candidate),        'Both alternatives are deliberately in the synthetic vocabulary'
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=300,height=100)
        page.insert_text((20,50),'Read the '+printed+' sample now',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    source[2]['text']=candidate
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text=candidate,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model+' sample now').split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']==status
    assert result['marked']=={2}
    if status=='UNRESOLVED':
        assert result['tokens'][2]==model
        assert note.get('source_image_only')
        assert note['method']=='unproved_shared_stroke_identity'
    else:assert result['tokens'][2]==printed


def test_visible_native_strokes_remain_authoritative_over_a_model_conflation():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the barn sample now')
    result=pa.adjudicate('Read the bam sample now'.split(),source,
        pa.CropWitness(source),source_print=True)
    assert result['tokens'][2]=='barn'
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['word_notes'][2]['method']=='visible_mapped_native_glyphs'


@pytest.mark.parametrize('source_token,model_token,read_token,native,status',[
    ('ABC','A.B.C.','ABC',False,'UNRESOLVED'),
    ('A.B.C.','ABC','ABC',False,'UNRESOLVED'),
    ('ABC','A.B.C.','A.B.C.',False,'RESOLVED'),
    ('ABC','A.B.C.','ABC',True,'RESOLVED'),
    ('A.B.C.','A.B.C.','ABC',False,'UNCHANGED'),
])
def test_omitted_initialism_dots_need_positive_print_evidence(
        source_token,model_token,read_token,native,status):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    printed=source_token if native else 'A.B.C.'
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=300,height=100)
        page.insert_text((20,50),'Read the '+printed+' sample now',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    source[2]['text']=source_token
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness[2]['text']=read_token
    witness.word_readings=lambda word:[[dict(word,text=read_token,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model_token+' sample now').split(),source,witness,source_print=native)
    if status=='UNCHANGED':
        assert result['tokens'][2]==model_token and not result['marked']
    else:
        note=result['word_notes'][2]
        assert note['status']==status
        assert result['marked']=={2}
        if status=='UNRESOLVED':
            assert note.get('source_image_only')
            assert note['method']=='unproved_initialism_punctuation'
        else:assert result['tokens'][2]==printed


@pytest.mark.parametrize('printed,model,candidate,native,status',[
    ('±','±','+',False,'UNRESOLVED'),
    ('x±','x±','x+',False,'UNRESOLVED'),
    ('⊕','⊕','+',False,'UNRESOLVED'),
    ('+','±','+',True,'RESOLVED'),
    ('±','+','±',False,'UNRESOLVED'),
])
def test_unchanged_alphanumeric_part_does_not_authorize_a_different_math_glyph(
        printed,model,candidate,native,status):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+candidate+' sample now')
    # Synthetic printed glyph identity is independent of the simulated optical
    # mistake. A native mapped occurrence retains its earlier authority path.
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text=candidate,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model+' sample now').split(),source,witness,source_print=native)
    if native:
        assert result['tokens'][2]==printed
        assert not result.get('word_notes',{}).get(2,{}).get('source_image_only')
        return
    note=result['word_notes'][2]
    assert note['status']==status
    assert result['marked']=={2}
    if status=='UNRESOLVED':
        assert note.get('source_image_only')
        assert note['method']=='unproved_mathematical_symbol_identity'
    else:assert result['tokens'][2]==printed


@pytest.mark.parametrize('printed,cropped,scope_failure',[
    ('grain',True,False),('rain',False,False),('grain',True,True),
])
def test_a_truncated_source_box_cannot_certify_deleting_a_complete_model_prefix(
        tmp_path,printed,cropped,scope_failure):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    drawing=pymupdf.open();page=drawing.new_page(width=300,height=100)
    page.insert_text((20,50),'Read the '+printed+' sample now',fontsize=12)
    source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    complete=list(source[2]['bbox']);source[2]['text']='rain'
    if cropped:source[2]['bbox'][0]+=(complete[2]-complete[0])*.25
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text='rain',confidence=99)]]*2
    # Independent print geometry supplies the full occurrence, rather than
    # accepting the repeated recognition of its truncated tail as that proof.
    witness.word_scope=lambda word:None if scope_failure else complete
    result=pa.adjudicate('Read the grain sample now'.split(),source,witness)
    note=result['word_notes'][2]
    if cropped and not scope_failure:
        assert result['tokens'][2]=='grain'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
    elif cropped:
        assert note['status']=='UNRESOLVED'
        assert note.get('source_image_only')
        assert note['method']=='unproved_complete_word_scope'
        if not scope_failure:assert note['source_bbox']==complete
    else:
        assert result['tokens'][2]=='rain'
        assert note['status']=='RESOLVED','Complete printed evidence can still remove an invented prefix'
    assert result['marked']=={2}
    drawing.close()


def test_lazy_scope_reads_original_ink_and_survives_the_terminal_token_view(monkeypatch,tmp_path):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=200,height=100)
        page.insert_text((60,50),'grain',fontsize=14)
        word=page.get_text('words')[0]
        clipped=dict(text='rain',bbox=list(word[:4]),confidence=99)
        clipped['bbox'][0]+=(clipped['bbox'][2]-clipped['bbox'][0])*.25
        monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:[dict(clipped)])
        witness=pa.read_crop(SourceDisplay(drawing,0),[clipped['bbox']],tmp_path)
        before=list(clipped['bbox'])
        scope=witness.word_scope(clipped)
        assert scope is not None and scope[0]<before[0]-1
        assert clipped['bbox']==before,'Measuring surrounding ink cannot mutate the original occurrence'
        normalized=pa._terminal_word_view(['grain'],witness)
        assert normalized.word_scope is witness.word_scope
        assert normalized.word_scope(clipped)==scope


@pytest.mark.parametrize('space,parts,paired',[
    (False,['gar','den'],True),(True,['gar','den'],True),
    (None,['gar','den'],True),(False,['gar','den'],False),
    (False,['If','a'],True),
])
def test_complete_word_networks_need_printed_boundary_proof_to_join_model_parts(
        monkeypatch,space,parts,paired):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(
        'inspect this garden example now if a ifa'.split()))
    joined=''.join(parts)
    source=words('Inspect this '+joined+' example now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:([[dict(word,confidence=99)]]*2 if paired else [])
    witness.word_separation=lambda word,tokens:space
    result=pa.adjudicate(['Inspect','this',*parts,'example','now'],source,witness)
    if space is False and parts==['gar','den']and paired:
        assert result['tokens']==['Inspect','this','garden','example','now']
        assert result['word_notes'][2]['status']=='RESOLVED'
        assert result['marked']=={2},'The actual lexical correction keeps its own source record'
    else:
        assert result['tokens']==['Inspect','this',*parts,'example','now']
        assert all(note['status']=='UNRESOLVED'for note in result['word_notes'].values())


@pytest.mark.parametrize('printed',['garden','gar den'])
def test_lazy_word_boundary_measurement_declines_ambiguous_or_printed_spaces(monkeypatch,tmp_path,printed):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=200,height=100)
        page.insert_text((60,50),printed,fontsize=14)
        boxes=page.get_text('words')
        word=dict(text='garden',confidence=99,bbox=[min(b[0]for b in boxes),min(b[1]for b in boxes),max(b[2]for b in boxes),max(b[3]for b in boxes)])
        monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:[word])
        witness=pa.read_crop(SourceDisplay(drawing,0),[word['bbox']],tmp_path)
        measure=pa._terminal_word_view(['gar','den'],witness).word_separation(word,['gar','den'])
        if printed=='garden':assert measure is False
        else:assert measure is not False,'A real or ambiguous space can never authorize a joined word'


@pytest.mark.parametrize('printed',['rain','grain'])
@pytest.mark.parametrize('raster',[False,True])
def test_small_print_scope_excludes_the_following_separate_word(monkeypatch,tmp_path,printed,raster):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=200,height=100)
        page.insert_text((40,50),printed+' while comparing samples.',fontsize=6)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        if raster:
            image=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
            drawing.delete_page(0);page=drawing.new_page(width=200,height=100)
            page.insert_image(page.rect,stream=image)
        source[0]['text']='rain'
        if printed=='grain':source[0]['bbox'][0]+=.25*(source[0]['bbox'][2]-source[0]['bbox'][0])
        monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:[dict(w)for w in source])
        witness=pa.read_crop(SourceDisplay(drawing,0),[source[0]['bbox']],tmp_path)
        witness.word_readings=lambda word:[[dict(word,text='rain',confidence=99)]]*2
        result=pa.adjudicate('grain while comparing samples.'.split(),source,witness)
        note=result['word_notes'][0]
        if printed=='rain':
            assert result['tokens'][0]=='rain'
            assert note['status']=='RESOLVED','Following words cannot invalidate a complete small printed occurrence'
        else:
            assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        scope=witness.word_scope(source[0])
        assert scope is not None and scope[2]<source[1]['bbox'][0]

@pytest.mark.parametrize('doubt,complete_other',[(False,False),(True,False),(False,True)])
def test_only_a_complete_competing_word_can_doubt_a_confident_vision_word(doubt,complete_other):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the rain sample now')
    # A tall detached stroke is part of the same printed word; the optical
    # token owns only its lower tail. Repeated confidence does not widen it.
    tail=list(source[2]['bbox']);whole=[tail[0]-4,tail[1]-5,tail[2],tail[3]]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_scope=lambda word:whole
    witness.word_readings=lambda word:([[dict(word,confidence=99)]]*2 +
        ([[dict(word,text='train',bbox=whole,confidence=99)]]*2 if complete_other else []))
    result=pa.adjudicate('Read the grain sample now'.split(),source,witness,
        uncertain=[2]if doubt else [])
    note=result['word_notes'][2]
    if not doubt and not complete_other:
        assert result['tokens'][2]=='grain'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert result['boxes'][2]==whole,'The retained occurrence must include its complete measured ink'
    else:
        assert note['status']=='UNRESOLVED','Real first-reader doubt or a complete alternative cannot be cleared by an incomplete tail'
    assert result['marked']=={2},'An actual optical-to-model correction retains its word-level provenance'


def test_original_ink_scope_keeps_detached_upper_and_leading_strokes():
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=150,height=100)
        for box in ([45,48,46.5,60],[49,46,50.3,47],[50,52,56,60],
                    [57,52,60,60],[66,52,70,60]):
            page.draw_rect(box,color=None,fill=(0,0,0))
        scope=pa._word_ink_scope(SourceDisplay(drawing,0),dict(bbox=[50,52,60,60]))
        assert scope is not None
        assert scope[0]<=45.5 and scope[1]<=46.5
        assert scope[2]<66,'A separate following word must stay outside the occurrence'
        assert not pa._covers_ink_scope([dict(bbox=[45,52,60,60])],scope), 'Complete horizontal coverage cannot compensate for missing upper strokes'


@pytest.mark.parametrize('conflict',['none','different_head','other_owner'])
def test_source_proved_note_head_expands_before_word_adjudication(tmp_path,monkeypatch,conflict):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    head='The example data come from a printed reference'
    tail='at noon near the wide open garden gate.'
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        leaf=drawing.new_page(width=400,height=250)
        leaf.insert_text((40,170),head,fontsize=10)
        leaf.insert_text((25,183),tail,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        original=leaf.get_pixmap().tobytes('png')
        doc.new_page(width=400,height=250).insert_image(leaf.rect,stream=original)
        assert not doc[0].get_text()
        text=(head if conflict!='different_head'else head.replace('The','Those'))+' '+tail
        own=block(text,ident='n1',role='footnote',marker='1',bbox=(50,696,900,760))
        answer={'blocks':[own]}
        if conflict=='other_owner':answer['blocks'].append(block(head,ident='b2',bbox=(75,625,900,690)))
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness([dict(w)for w in source if any(vp.owned_words([w],b)for b in boxes)]))
        page,report=vp.compile_page(answer,source,SourceDisplay(doc,0),0,Package(),{},pixel_words=source,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=next(b for b in report['blocks']if b['id']=='n1')
        expanded=any(g['kind']=='complete_note_head_territory'for g in record['guard'])
        if conflict=='none':
            assert record['published_tokens']==(head+' '+tail).split()
            assert expanded
            assert not record['marked'],'A clipped crop cannot make every correct first-line word uncertain'
            first=source[0]['bbox']
            assert any(b[0]<=first[0]and b[1]<=first[1]and b[2]>=first[2]and b[3]>=first[3]for b in record['source_regions'])
        else:assert not expanded,'Different words or another source owner cannot expand this note'


@pytest.mark.parametrize('model,counter', [('reef','rccf'),('fold','lold'),('yarn','varn')])
def test_shared_eroded_strokes_cannot_certify_a_changed_complete_word(monkeypatch,model,counter):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    # Vocabulary membership cannot distinguish a missing crossbar or descender.
    # Both injected networks repeat the same synthetic optical error.
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(
        {'read','the','sample','now',model,counter}))
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=300,height=100)
        page.insert_text((20,50),'Read the '+model+' sample now',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
    source[2]['text']=counter
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text=counter,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model+' sample now').split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED','Shared recognition cannot prove a missing distinguishing stroke'
    assert note.get('source_image_only')and note['method']=='unproved_shared_stroke_identity'
    assert result['tokens'][2]==model and result['marked']=={2}


@pytest.mark.parametrize('printed,first',[('rccf','reef'),('lold','fold'),('varn','yarn')])
def test_visible_native_eroded_stroke_alternative_remains_authoritative(printed,first):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+printed+' sample now')
    result=pa.adjudicate(('Read the '+first+' sample now').split(),source,
        pa.CropWitness(source),source_print=True)
    assert result['tokens'][2]==printed
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['word_notes'][2]['method']=='visible_mapped_native_glyphs'


@pytest.mark.parametrize('position,source_quote',[('adjacent','”'),('distant','”'),('adjacent','’')])
def test_separated_matching_closing_quote_is_formatting_only_at_its_own_word(position,source_quote):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read this sample.')
    box=list(source[-1]['bbox']);height=box[3]-box[1]
    quote=dict(text=source_quote,bbox=[box[2]+3,box[1],box[2]+6,box[1]+3],confidence=57)
    if position=='distant':quote['bbox']=[box[2]+10*height,box[1],box[2]+10*height+3,box[1]+3]
    source.append(quote)
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    result=pa.adjudicate(['Read','this','sample.”'],source,witness)
    if position=='adjacent'and source_quote=='”':
        assert result['tokens']==['Read','this','sample.”']
        assert not result['marked'],'A matching separately printed closing mark is not a different reading'
        assert result['boxes'][2][2]>=quote['bbox'][2]
    else:
        assert result['marked'],'A distant or different quotation mark cannot settle the model reading'


@pytest.mark.parametrize('printed,first',[('reef','rccf'),('fold','lold'),('yarn','varn')])
def test_positive_pixel_stroke_is_distinct_from_certifying_its_absence(monkeypatch,printed,first):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(
        {'read','the','sample','now',printed,first}))
    source=words('Read the '+printed+' sample now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text=printed,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+first+' sample now').split(),source,witness)
    assert result['tokens'][2]==printed
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert not result['word_notes'][2].get('source_image_only')


@pytest.mark.parametrize('case',['matching','different','other_owner','no_pixels'])
def test_clipped_closing_mark_requires_its_unclaimed_visible_occurrence(tmp_path,monkeypatch,case):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        page=drawing.new_page(width=300,height=150)
        page.insert_text((30,70),'Read this clear sample. "',fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        quote=source[-1];quote['confidence']=57
        pixels=[dict(w)for w in source if case!='no_pixels'or w is not quote]
        if case=='different':pixels[-1]['text']='’'
        body=source[:-1];right=max(w['bbox'][2]for w in body)+.5
        image=page.get_pixmap().tobytes('png')
        doc.new_page(width=300,height=150).insert_image(page.rect,stream=image)
        assert not doc[0].get_text()
        own=block('Read this clear sample.”',ident='b1',bbox=(90,350,right/300*1000,500))
        answer={'blocks':[own]}
        if case=='other_owner':
            q=quote['bbox'];answer['blocks'].append(block('"',ident='b2',bbox=(q[0]/300*1000,350,900,500)))
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness([dict(w)for w in pixels if any(vp.owned_words([w],b)for b in boxes)]))
        page,report=vp.compile_page(answer,source,SourceDisplay(doc,0),0,Package(),{},pixel_words=pixels,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=next(b for b in report['blocks']if b['id']=='b1')
        widened=any(g['kind']=='complete_closing_mark_territory'for g in record['guard'])
        if case=='matching':
            assert widened
            assert record['published_tokens']==['Read','this','clear','sample.”']
            assert not record['marked']and not record.get('source_image_words')
            assert any(b[2]>=quote['bbox'][2]for b in record['source_regions'])
        else:assert not widened,'A different, unrecognized or already owned mark cannot widen this source'


@pytest.mark.parametrize('model,counter',[('willow','villow'),('Aim','\\im'),('A','\\')])
def test_missing_letter_strokes_cannot_be_certified_as_a_slash_or_half_letter(monkeypatch,model,counter):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(
        {'read','the','sample','now','im',model.casefold(),counter.casefold()}))
    source=words('Read the '+counter+' sample now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text=counter,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model+' sample now').split(),source,witness)
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert note['method']=='unproved_shared_stroke_identity'
    assert result['tokens'][2]==model and result['marked']=={2}


@pytest.mark.parametrize('printed,first,cropped',[('rose','roset',False),('prose','prose',True)])
def test_other_source_rows_cannot_enter_complete_word_ink_scope(tmp_path,monkeypatch,printed,first,cropped):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        page=drawing.new_page(width=300,height=140)
        for y,text in [(50,'Different green leaves fill this row'),(62,'Read the '+printed+' sample now'),(74,'Separate lines stay separate here')]:
            page.insert_text((30,y),text,fontsize=8)
        all_words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        local=[w for w in all_words if 52<w['bbox'][1]<55]
        assert len(local)==5
        source=[dict(w)for w in local]
        if cropped:
            source[2]['bbox']=list(source[2]['bbox']);source[2]['bbox'][0]+=.2*(source[2]['bbox'][2]-source[2]['bbox'][0]);source[2]['text']='rose'
        image=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
        doc.new_page(width=300,height=140).insert_image(page.rect,stream=image)
        monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:[dict(w)for w in all_words])
        display=SourceDisplay(doc,0);witness=pa.read_crop(display,[source[2]['bbox']],tmp_path)
        witness.model_reading=True
        witness.word_readings=lambda word:[[dict(word,text='rose',confidence=99)]]*2
        result=pa.adjudicate(('Read the '+first+' sample now').split(),source,witness)
        assert result['tokens'][2]==printed
        if not cropped:
            note=result['word_notes'][2]
            assert note['status']=='RESOLVED'and not note.get('source_image_only')
            assert 2 in result['marked'],'The corrected extra model letter retains provenance'
        else:
            assert not any(d['status']=='RESOLVED'and d['chosen']==['rose']for d in result['decisions'])
        scope=witness.word_scope(source[2]);assert scope is not None
        assert scope[1]>52 and scope[3]<65,'Other printed rows cannot enlarge this word scope'


@pytest.mark.parametrize('space,uncertain',[(True,False),(False,False),(None,False),(True,True)])
def test_printed_spaces_settle_complete_model_words_without_neighbor_uncertainty(space,uncertain):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read As a sample now.')
    source[1]['text']='Asa';source[1]['bbox']=[20,20,58,32];del source[2]
    witness=pa.CropWitness([dict(w)for w in source])
    witness.model_reading=True
    witness.word_separation=lambda word,parts:space
    result=pa.adjudicate('Read As a sample now.'.split(),source,witness,uncertain={1}if uncertain else ())
    assert result['tokens']=='Read As a sample now.'.split()
    if space is True and not uncertain:
        assert result['marked']==set(),'A proved space changes no model word and disputes no neighbor'
        assert result['word_notes']=={}
        assert result['decisions']==[]
        assert set(result['boxes'])==set(range(5))
    else:
        assert result['marked'],'Absent space proof or explicit reader doubt remains disclosed'
        assert any(d['status']=='UNRESOLVED'for d in result['decisions'])


def test_late_alphabet_candidate_cannot_invent_an_unobserved_internal_quote(monkeypatch):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:{'clear','cear'})
    source=words('Read the Cear sample now.')
    witness=pa.CropWitness([dict(w)for w in source])
    altered=dict(source[2],text="C'ear",confidence=99)
    witness.word_readings=lambda word:[[altered],[altered]]
    result=pa.adjudicate('Read the Clear sample now.'.split(),source,witness)
    assert result['tokens'][2]=='Clear'
    note=result['word_notes'][2]
    assert note['status']=='UNRESOLVED'and note['source_image_only']
    assert note['method']=='unproved_quote_identity'
    assert not any(d['status']=='RESOLVED'and d['chosen']==["C'ear"]for d in result['decisions'])


@pytest.mark.parametrize('first,candidate,vocabulary,unproved',[
    ('conem','concem',{'concern'},True),
    ('gardcn','garden',{'garden'},False),
    ('conem','concem',{'concem'},False),
])
def test_unfamiliar_joined_stroke_candidate_keeps_possible_unseen_letters_unresolved(monkeypatch,first,candidate,vocabulary,unproved):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:vocabulary)
    source=words('Read the '+candidate+' sample now.')
    witness=pa.CropWitness([dict(w)for w in source])
    witness.word_readings=lambda word:[[source[2]],[source[2]]]
    result=pa.adjudicate(('Read the '+first+' sample now.').split(),source,witness)
    note=result['word_notes'][2]
    if unproved:
        assert result['tokens'][2]==first
        assert note['status']=='UNRESOLVED'and note['source_image_only']
        assert note['method']=='unproved_shared_stroke_identity'
        assert result['tokens'][2]!='concern','The dictionary supplies no replacement letters'
    else:
        assert result['tokens'][2]==candidate
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
@pytest.mark.parametrize('inserted',[['bright'],['soft','blue']])
@pytest.mark.parametrize('replacement',[[],['silver']])
@pytest.mark.parametrize('proved',[False,True])
def test_unproved_source_changes_have_their_own_original_ink(inserted,replacement,proved):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Keep the '+' '.join(inserted)+' sample ready now.')
    for word in source[2:2+len(inserted)]:word['confidence']=40
    witness=pa.CropWitness([dict(w,confidence=99)for w in source]if proved else [])
    model=['Keep','the',*replacement,'sample','ready','now.']
    result=pa.adjudicate(model,source,witness,unresolved_source=bool(replacement))
    assert result['tokens']==[w['text']for w in source]
    for index in range(2,2+len(inserted)):
        note=result['word_notes'][index]
        assert note['status']==('RESOLVED'if proved else'UNRESOLVED')
        assert bool(note.get('source_image_only'))is not proved
        if not proved:
            assert note['source_bbox']==source[index]['bbox']
            assert result['boxes'][index]==source[index]['bbox']
            assert note['source']==[source[index]['text']]
            if not replacement:assert note['model']==[]


@pytest.mark.parametrize('proved',[False,True])
def test_compilation_cannot_publish_an_unproved_source_insertion_as_live_text(tmp_path,monkeypatch,proved):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
        def disclosure_image(self,name,display,rect):
            self.images[name]=display.pixmap(rect,scale=2,max_pixels=100000).tobytes('jpg')
    package=Package()
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        leaf=drawing.new_page(width=320,height=150)
        leaf.insert_text((25,60),'Keep the bright sample ready now.',fontsize=11)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        source[2]['confidence']=40
        raster=leaf.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
        doc.new_page(width=320,height=150).insert_image(leaf.rect,stream=raster)
        assert not doc[0].get_text(),'Native paint authority is a separate positive control'
        monkeypatch.setattr(pa,'read_crop',lambda *args:pa.CropWitness(
            [dict(w,confidence=99)for w in source]if proved else []))
        page,report=vp.compile_page({'blocks':[block('Keep the sample ready now.',bbox=(0,0,1000,1000))]},
            source,SourceDisplay(doc,0),0,package,{},pixel_words=source,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    own=report['blocks'][0]
    assert own['published_tokens']=='Keep the bright sample ready now.'.split()
    body=' '.join(page['body'])
    if proved:
        assert 'bright'in vp._seam_text(body)
        assert own['source_image_words']==[]
        assert all(d['status']=='RESOLVED'for d in own['adjudication'])
    else:
        from tests.unit.test_reflow_reading_tiers import assert_flagged_print
        assert_flagged_print(body, ['bright'])
        assert own['source_image_words']==[]
        assert any(d['status']=='UNRESOLVED'for d in own['adjudication']), 'Assistance is not a fabricated proof'
        assert body.count('class="source-word-image"')==1
        assert package.images,'The actual original word must remain inspectable'


@pytest.mark.parametrize('model,optical',[('★','&'),('÷',':')])
@pytest.mark.parametrize('native',[False,True])
def test_symbol_to_punctuation_is_not_empty_letter_agreement(model,optical,native):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the '+optical+' sample now.')
    witness=pa.CropWitness([dict(w)for w in source])
    witness.word_readings=lambda word:[[dict(word,confidence=99)]]*2
    result=pa.adjudicate(('Read the '+model+' sample now.').split(),source,witness,
        unresolved_source=not native,source_print=native)
    note=result['word_notes'].get(2,{})
    if native:
        assert result['tokens'][2]==optical
        assert not note or note['status']=='RESOLVED'and note['method']=='visible_mapped_native_glyphs'
    else:
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert note['source_bbox']==source[2]['bbox']


@pytest.mark.parametrize('refinement_note',[False,True])
def test_fallback_refinement_keeps_original_ink_for_an_unchanged_unproved_word(tmp_path,monkeypatch,refinement_note):
    """A later inconclusive pass supplies no new authority for the same word."""
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    source=words('Keep the bright blue wooden sample ready now.')
    for word in source[2:5]:word['confidence']=40
    calls=[]
    def refine(tokens,*args,**kwargs):
        calls.append(list(tokens))
        index=tokens.index('bright')
        note=dict(source=['bright'],model=['bright'],chosen=['bright'],
            status='UNRESOLVED',method='pixel_read_inconclusive')
        return dict(tokens=list(tokens),boxes={},marked={index}if refinement_note else set(),
            word_notes={index:note}if refinement_note else{},decisions=[note]if refinement_note else[])
    monkeypatch.setattr(pa,'read_crop',lambda *args:pa.CropWitness([]))
    monkeypatch.setattr(pa,'refine_fallback',refine)
    class Package:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
        def disclosure_image(self,name,display,rect):
            self.images[name]=display.pixmap(rect,scale=2,max_pixels=100000).tobytes('jpg')
    package=Package()
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        leaf=drawing.new_page(width=320,height=150)
        leaf.insert_text((25,60),'Keep the bright blue wooden sample ready now.',fontsize=11)
        raster=leaf.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
        doc.new_page(width=320,height=150).insert_image(leaf.rect,stream=raster)
        page,report=vp.compile_page({'blocks':[block('Keep the sample ready now.',bbox=(0,0,1000,1000))]},
            source,SourceDisplay(doc,0),0,package,{},pixel_words=source,forced_uncertain=('b1',),
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert calls,'Exercise the later fallback refinement rather than bypassing it'
    record=report['blocks'][0]
    assert record['source_image_words']==[]
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    assert_flagged_print(''.join(page['body']), ['bright','blue','wooden'])
    assert all(d['status']=='UNRESOLVED'for d in record['adjudication'])
    assert 'sample ready now.'in vp._seam_text(' '.join(page['body']))
    assert package.images,'The complete unproved phrase retains its inspectable original ink'
    for meta in report['blocks']:
        assert set(map(int,meta['flagged_readings']))=={2,3,4}


@pytest.mark.parametrize('binding',['own','remote','repeated'])
def test_independent_word_occurrences_split_a_damaged_multiword_alignment(binding):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Keep the c1car bluc sarnple ready now.')
    model='Keep the clear blue sample ready now.'.split()
    # The final context differs, so it cannot certify this whole edit as one
    # span. Each complete physical word needs its own recognition instead.
    rows=words('Keep the clear blue sample ready later.')
    if binding=='remote':
        rows=[dict(w,bbox=[w['bbox'][0],60,w['bbox'][2],72])for w in rows]
    if binding=='repeated':
        model='Keep the clear clear sample ready now.'.split()
        rows[3]=dict(rows[3],text='clear')
    witness=pa.CropWitness(rows)
    witness.word_readings=lambda word:[[dict(w)for w in rows]]*2
    result=pa.adjudicate(model,source,witness)
    if binding=='own':
        assert result['tokens']==model
        for index in (2,3,4):
            note=result['word_notes'][index]
            assert note['status']=='RESOLVED'
            assert not note.get('mapping'),'Do not leave a positively placed word in an unplaced edit span'
            assert result['boxes'][index]==source[index]['bbox']
            assert note['chosen']==[model[index]]
    else:
        assert not all(result['word_notes'].get(i,{}).get('status')=='RESOLVED'for i in (2,3,4))
        assert result['marked'],'Remote or repeated spelling alone does not supply unique occurrence anchors'


@pytest.mark.parametrize('proof',['own','absent','different','remote'])
def test_weak_line_division_suffix_can_use_its_own_independent_word_read(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('con- tinuation')
    source[0]['bbox']=[100,20,130,32];source[1]['bbox']=[0,40,60,52]
    source[1]['confidence']=40
    witness=pa.CropWitness([dict(source[0])]);witness.model_reading=True
    def read(word):
        if proof=='absent':return []
        value=dict(word,confidence=99,text='tinuation'if proof!='different'else'tonation')
        if proof=='remote':value['bbox']=[0,80,60,92]
        return [[value],[value]]
    witness.word_readings=read
    result=pa.adjudicate(['continuation'],source,witness)
    assert result['tokens']==['continuation']
    if proof=='own':
        assert not result['marked'],'Every exact letter and the printed division now have their own proof'
        assert result['word_notes']=={}
        assert result['boxes'][0]==[0,20,130,52]
    else:
        assert result['marked']
        assert not any(d['status']=='RESOLVED'for d in result['decisions'])


@pytest.mark.parametrize('kind',['space','scope'])
def test_reference_projection_preserves_its_original_physical_word_proofs(kind):
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    source=words('Read Asa sample now. 1'if kind=='space'else'Read the rose sample now. 1')
    if kind=='space':
        source[1]['bbox']=[20,20,58,32]
        source[2]['bbox']=[60,20,78,32];source[3]['bbox']=[80,20,98,32]
    source[-1]['bbox']=[104,15,108,21];source[-1]['native_reference_ink']=True
    model=('Read As a sample now. <n:n1>'if kind=='space'else'Read the arose sample now. <n:n1>').split()
    proof={len(model)-1:dict(status='PROVED',label='1',source_bbox=source[-1]['bbox'])}
    witness=pa.CropWitness(source)
    witness.word_readings=lambda word:[[dict(word)],[dict(word)]]
    witness.word_separation=lambda word,parts:True
    witness.word_scope=lambda word:[35,20,58,32]
    tokens,lexical=vp._lexical_marker_view(model,source,proof)
    _,projected=vp._lexical_marker_view(model,witness,proof)
    projected.model_reading=True
    result=pa.adjudicate(tokens,lexical,projected)
    assert result['tokens']==model,'A reserved reference cannot make the adjacent complete word lose its prefix'
    assert result['tokens'].count('<n:n1>')==1
    if kind=='space':
        assert not result['marked'],'Already proved original spaces are not unresolved lexical edits'
        assert result['decisions']==[]
    else:
        assert result['word_notes'][2]['method']=='complete_vision_reading_without_optical_counterevidence'
        assert not any(d['status']=='RESOLVED'and d['chosen']==['rose']for d in result['decisions'])


@pytest.mark.parametrize('confidence',[40,99,None])
@pytest.mark.parametrize('counter',[False,True])
def test_source_conservation_withholds_only_the_specific_weak_or_contested_word(confidence,counter):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Keep the bright blue sample ready now.')
    if confidence is None:source[2].pop('confidence')
    else:source[2]['confidence']=confidence
    witness=pa.CropWitness([dict(source[2],text='light',confidence=99)]if counter else [])
    result=pa.adjudicate('Keep the sample ready now.'.split(),source,witness)
    assert result['tokens']==[w['text']for w in source]
    assert bool(result['word_notes'][2].get('source_image_only'))is(confidence==40 or counter)
    assert not result['word_notes'][3].get('source_image_only'),'The neighboring admitted word is not implicated'
    assert result['word_notes'][2]['status']=='UNRESOLVED','Absence of a second read certifies no correction'
    if confidence==40 or counter:assert result['word_notes'][2]['source_bbox']==source[2]['bbox']


@pytest.mark.parametrize('collision',[False,True])
@pytest.mark.parametrize('confidence',[None,99])
def test_conserved_source_labels_require_separate_physical_occurrences(collision,confidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Keep the sample ready. 1 2')
    if collision:source[-1]['bbox']=list(source[-2]['bbox'])
    for word in source[-2:]:
        if confidence is None:word.pop('confidence')
        else:word['confidence']=confidence
    result=pa.adjudicate('Keep the sample ready.'.split(),source,pa.CropWitness())
    assert result['tokens']==[w['text']for w in source]
    assert result['marked']=={4,5}
    for index in (4,5):
        note=result['word_notes'][index]
        assert bool(note.get('source_image_only'))is collision
        assert note['status']=='UNRESOLVED'
        if collision:assert note['source_bbox']==source[index]['bbox']
    assert all(i not in result['word_notes']for i in range(4))


@pytest.mark.parametrize('confidence',[86,99])
def test_a_whole_word_anchor_cannot_leave_its_own_source_suffix_as_an_extra_word(confidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the gardener s sample now.')
    source[2]['bbox']=[40,20,86,32];source[3]['bbox']=[87,20,90,32]
    source[4]['bbox']=[92,20,110,32];source[5]['bbox']=[112,20,130,32]
    whole=dict(text='gardener’s',bbox=[40,20,90,32],confidence=confidence)
    witness=pa.CropWitness([dict(source[0]),dict(source[1]),whole,dict(source[4]),dict(source[5])])
    result=pa.adjudicate('Read the gardener’s sample now.'.split(),source,witness)
    assert result['tokens']=='Read the gardener’s sample now.'.split()
    assert 's'not in result['tokens'],'The same recognized word already accounts for its printed suffix'
    assert result['word_notes'][2]['source']==['gardener','s']
    assert result['word_notes'][2]['status']=='RESOLVED'
    assert result['boxes'][2]==whole['bbox']


@pytest.mark.parametrize('symbol',['*','†','‡'])
def test_an_unplaced_reference_suffix_cannot_split_off_its_ocr_letter(symbol):
    """Synthetic reference ink is t in OCR; the next lexical word is disputed."""
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Clear samples t but remain readable.')
    witness=pa.CropWitness([dict(w,text='and'if w['text']=='but'else w['text'])
        for w in source if w['text']!='t'])
    result=pa.adjudicate(['Clear','samples'+symbol,'<n:note>','and','remain','readable.'],source,witness)
    assert result['tokens']==['Clear','samples'+symbol,'<n:note>','and','remain','readable.']
    assert 't'not in result['tokens'],'a glyph alternative cannot become an extra lexical occurrence'


@pytest.mark.parametrize('evidence',['complete','partial','credible_competitor','local','correction'])
def test_whole_word_corroboration_precedes_noisy_alternate_image_fallback(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clear sample stays readable.',40)
    noise=words('The clear sampie stays readable.',88)
    witness=pa.CropWitness(noise)
    witness.alternates=lambda:[noise,noise]
    positive=words('The clear sample stays readable.',95)
    other=words('The clear simple stays readable.',92)if evidence=='credible_competitor'else noise
    if evidence=='partial':positive[2]['bbox']=[47,20,58,32]
    witness.word_readings=lambda word:[positive,other]
    note=dict(source=['sampie']if evidence=='correction'else['sample'],model=['sample'],
        chosen=['sample'],alternative=['sampie'],status='UNRESOLVED',method='agreeing_readings_pixel_conflict')
    result=dict(tokens=[w['text']for w in source],marked={2},word_notes={2:note})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness,
        allow_clear=evidence!='local')
    assert result['tokens']==[w['text']for w in source]
    if evidence=='complete':
        assert result['marked']==set()
        assert result['word_notes']=={}
        assert result['verified_unchanged']==[2]
    else:
        assert result['marked']=={2}
        assert result['word_notes'][2]['status']=='UNRESOLVED'


@pytest.mark.parametrize('evidence',['complete','partial','disagreeing','unflagged','proper_name'])
def test_weak_agreeing_ocr_can_request_its_own_complete_word_reading(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    token='Xample'if evidence=='proper_name'else'xample'
    source=words('Read the '+token+' beside this box.',40)
    witness=pa.CropWitness([])
    positive=words('Read the sample beside this box.',95)
    second=words('Read the simple beside this box.',95)if evidence=='disagreeing'else positive
    if evidence=='partial':positive[2]['bbox']=[47,20,58,32]
    witness.word_readings=lambda word:[positive,second]
    result=pa.adjudicate([w['text']for w in source],source,witness,
        uncertain=set()if evidence=='unflagged'else{2})
    if evidence=='complete':
        assert result['tokens'][2]=='sample'
        assert result['word_notes'][2]['status']=='RESOLVED'
        assert result['word_notes'][2]['source']==[token]
        assert result['word_notes'][2]['chosen']==['sample']
        assert 2 in result['marked']
    else:
        assert result['tokens'][2]==token
        assert not any(d['status']=='RESOLVED'for d in result['decisions'])


@pytest.mark.parametrize('source_text,model_text',[
    ('Read the xample beside this box.','Read the helpful sample beside this box.'),
    ('Read the l. owly sample now.','Read the very useful sample now.')])
def test_an_unproved_multiword_rewrite_cannot_leave_invented_words_live(source_text,model_text):
    from cps.services.reflow import pixel_adjudication as pa
    source=words(source_text,40)
    witness=pa.CropWitness([])
    witness.model_reading=True
    result=pa.adjudicate(model_text.split(),source,witness)
    assert result['tokens']==source_text.split()
    images={i for i,d in result['word_notes'].items()if d.get('source_image_only')}
    expected={i for i,t in enumerate(source_text.split())if t in ('xample','l.','owly')}
    assert images==expected
    assert all(result['word_notes'][i]['source_bbox']==source[i]['bbox']for i in images)
    assert all(result['word_notes'][i]['status']=='UNRESOLVED'for i in images)
    assert result['marked']==expected


@pytest.mark.parametrize('evidence',['complete','partial','competing_pair','weak'])
def test_two_complete_word_networks_can_verify_an_unchanged_invented_name(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Fennwick repaired this green latch.',40)
    noise=words('Fenmvick repaired this green latch.',45)
    witness=pa.CropWitness(noise);witness.alternates=lambda:[noise,noise]
    first=words('Fennwick repaired this green latch.',95)
    second=words('Fennwick repaired this green latch.',86 if evidence!='weak'else 40)
    if evidence=='partial':first[0]['bbox']=[7,20,18,32]
    opposite=words('Fenmvick repaired this green latch.',95)
    witness.word_readings=lambda word:[first,second]+([opposite,opposite]if evidence=='competing_pair'else[])
    note=dict(source=['Fennwick'],model=['Fennwick'],chosen=['Fennwick'],
        status='UNRESOLVED',method='reader_uncertainty')
    result=dict(tokens=[w['text']for w in source],marked={0},word_notes={0:note})
    pa.disclose_alternate_conflicts(result,{i:w['bbox']for i,w in enumerate(source)},witness)
    assert result['tokens']==[w['text']for w in source]
    if evidence=='complete':
        assert result['marked']==set()
        assert result['word_notes']=={}
        assert result['verified_unchanged']==[0]
    else:
        assert result['marked']=={0}
        assert result['word_notes'][0]['status']=='UNRESOLVED'


@pytest.mark.parametrize('boundary',['own','other_owner','different_tail','remote'])
def test_letter_split_note_tail_can_continue_on_same_row_and_wrap(boundary):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    opening='Fresh timber keeps this printed entry beside'
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=500,height=250)
        page.insert_text((30,160),opening,fontsize=10)
        for i,char in enumerate('FENNWICK,'):
            page.insert_text((235+i*6,160),char,fontsize=8)
        page.insert_text((295,160),'Repair-',fontsize=8)
        page.insert_text((35,171 if boundary!='remote'else 220),'manual 2.',fontsize=8)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        # Recognition can emit one atom per small-cap letter; print and widths
        # are independently drawn above. A soft hyphen retains the line break.
        source=source[:len(opening.split())]+[
            dict(text=char,bbox=[235+i*6,151.4,240+i*6,162.4],confidence=99)
            for i,char in enumerate('FENNWICK,')]+source[-3:]
        source[-3]['text']='Repair\u00ad'
        local=source[:-3]
        own=block(opening+' FENNWICK, Repairmanual '+('2.'if boundary!='different_tail'else'7.'),
            ident='n1',role='footnote',marker='1',bbox=(50,590,590,650))
        regions={'n1':[[25,148,292,163]],'_instances':{}}
        if boundary=='other_owner':regions['_instances'][vp._source_instance(source[-1])]='n2'
        result=vp._complete_note_tail_territory(own,{'blocks':[own]},source,SourceDisplay(drawing,0),regions,local)
        if boundary=='own':assert result==source,'Every exact adjacent tail atom must enter the adjudicated crop'
        else:assert result is None,'Foreign ownership, different letters and remote lines cannot extend a note'


@pytest.mark.parametrize('optical',['complete','changed_letter','partial','separate_words'])
def test_small_cap_fragments_remain_separate_from_a_neighboring_unproved_edit(optical):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The entry F E N N W I C K repairmanual stays here.')
    for i,w in enumerate(source[2:10]):w['bbox']=[40+i*6,20,45+i*6,32]
    for i,w in enumerate(source[10:]):w['bbox']=[100+i*40,20,138+i*40,32]
    baseline=pa.CropWitness([dict(w)for w in source[:2]]+[
        dict(text='FENNWICK'if optical!='changed_letter'else'FENNMICK',bbox=[40,20,87,32],confidence=99),
        *[dict(w)for w in source[10:]]])
    if optical=='partial':baseline[2]['bbox']=[55,20,87,32]
    if optical=='separate_words':
        source[2]['text']='Go';source[3]['text']='to'
    baseline.model_reading=True
    result=pa.adjudicate('The entry FENNWICK blue counter stays here.'.split(),source,baseline)
    if optical=='complete':
        assert result['tokens'][2]=='FENNWICK'
        assert 2 not in result.get('source_image_words',())
        assert not result['word_notes'].get(2,{}).get('source_image_only')
        assert 'blue'not in result['tokens']and'counter'not in result['tokens']
    else:
        assert not any(t=='FENNWICK'and not result['word_notes'].get(i,{}).get('source_image_only')
                       for i,t in enumerate(result['tokens']))


@pytest.mark.parametrize('evidence',['own','weak_own','different_occurrence','missing_word'])
def test_weak_neighbor_cannot_make_a_complete_independently_read_edit_unplaced(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('This hcre.')
    source[1]['bbox']=[30,20,98,32]
    witness=pa.CropWitness([
        dict(text='This',bbox=source[0]['bbox'],confidence=40),
        dict(text='stays',bbox=[30,20,60,32],confidence=99),
        dict(text='here.',bbox=[65,20,98,32],confidence=99)])
    if evidence=='weak_own':witness[1]['confidence']=40
    if evidence=='different_occurrence':
        for word in witness:word['bbox']=[v+(200 if i%2==0 else 0)for i,v in enumerate(word['bbox'])]
    if evidence=='missing_word':witness.pop(1)
    witness.model_reading=True
    result=pa.adjudicate('This stays here.'.split(),source,witness)
    if evidence=='own':
        assert result['tokens']==['This','stays','here.']
        assert result['marked']=={1,2},'Model corrections retain their individual source-linked records'
        assert all(note['status']=='RESOLVED'for note in result['word_notes'].values())
        assert result['boxes'][1]==witness[1]['bbox']and result['boxes'][2]==witness[2]['bbox']
    else:assert any(note['status']=='UNRESOLVED'for note in result['word_notes'].values())


@pytest.mark.parametrize('scope',['contained','cropped_recognition','outside','absent','credible_competitor'])
def test_measured_ink_inside_padding_can_prove_a_complete_word(scope):
    from cps.services.reflow import pixel_adjudication as pa
    original=dict(text='clear',bbox=[0,0,60,20],confidence=99)
    witness=pa.CropWitness()
    ink=[10,5,45,15]
    text='clean'if scope=='credible_competitor'else'clear'
    recognized=dict(text=text,bbox=[25,5,45,15]if scope=='cropped_recognition'else ink,confidence=99)
    witness.word_readings=lambda word:[[dict(recognized)],[dict(recognized)]]
    witness.word_scope=lambda word:(None if scope=='absent'else[-10,5,45,15]if scope=='outside'else ink)
    chosen=pa._model_word_choice(original,witness,'clear')
    if scope=='contained':
        assert chosen is not None and chosen['text']=='clear'
        assert pa._own_word_choice(original,witness)['text']=='clear'
    else:assert chosen is None,'Padding cannot waive cropped letters, remote ink or a credible alternative'


@pytest.mark.parametrize('prefix',['damaged','dam aged'])
def test_an_unproved_prefix_cannot_hide_a_conserved_punctuation_spaced_suffix(prefix):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read '+prefix+' Hill.(Q.minor,R.); Bay,Delta. now.')
    witness=pa.CropWitness([]);witness.model_reading=True
    result=pa.adjudicate('Read a different Hill. (Q. minor, R.); Bay, Delta. now.'.split(),source,witness)
    suffix='Hill. (Q. minor, R.); Bay, Delta. now.'.split()
    assert result['tokens'][-len(suffix):]==suffix
    assert all(i<len(result['tokens'])-len(suffix)for i in result['marked'])
    assert all(d.get('source_image_only')for d in result['word_notes'].values())
    assert 'different'not in result['tokens']


def test_a_conserved_letter_stream_does_not_certify_letter_to_letter_spaces():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read Ifa beside this box.')
    witness=pa.CropWitness([]);witness.model_reading=True
    result=pa.adjudicate('Read If a beside this box.'.split(),source,witness)
    assert result['marked'], 'A literal printed space still needs its own pixel proof'
    assert not result.get('verified_unchanged')or not {1,2}<=set(result['verified_unchanged'])


@pytest.mark.parametrize('evidence',['complete','partial','different'])
def test_unplaced_reference_ink_cannot_hide_its_independently_read_lexical_neighbor(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Observe pale stones, t beside this box.')
    source[3]['bbox']=[58,20,63,32]
    source[2]['bbox']=[40,20,57,32]
    witness=pa.CropWitness(words('Observe pale stones, beside this box.'))
    witness[2]['bbox']=([49,20,57,32]if evidence=='partial'else[40,20,57,32])
    if evidence=='different':witness[2]['text']='stone,'
    witness.model_reading=True
    result=pa.adjudicate('Observe pale stones,‡ <n:note> beside this box.'.split(),source,witness)
    if evidence=='complete':
        assert result['tokens'][2:4]==['stones,‡','<n:note>']
        assert not result['word_notes'].get(2,{}).get('source_image_only')
        assert 't'not in result['tokens']
    else:
        assert result['word_notes'][2].get('source_image_only')


def test_a_native_lexical_letter_cannot_be_consumed_as_a_guessed_reference():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Observe pale stones, t beside this box.')
    source[3]['bbox']=[58,20,63,32];source[2]['bbox']=[40,20,57,32]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    result=pa.adjudicate('Observe pale stones,‡ <n:note> beside this box.'.split(),source,witness,source_print=True)
    assert 't'in result['tokens'],'Reliably drawn native letters remain live at their own occurrence'
    assert 'stones,‡'not in result['tokens'],'An unproved reference must not replace the native letter'


def test_short_cross_column_regions_keep_their_admitted_reading_flow():
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from tests.unit.test_reflow_vision_pilot import _cross_column_optical_fixture,answer,block
    text,_,source=_cross_column_optical_fixture()
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,300,400));regions={}
    reading,_=vp.bind_geometry(answer(block(text,bbox=(40,846,949,302))),source,display,regions)
    assert len(regions['b1'])==2
    assert regions['b1'][0][1]>regions['b1'][1][1], 'Short consecutive columns have a source-proven return to the upper row'
    own=vp._owned_block_words(reading['blocks'][0],reading,source,display,regions,ordered=True)
    assert [w['text']for w in own]==text.split()


@pytest.mark.parametrize('evidence', [
    'complete_boundary', 'missing_pair', 'weak_pair', 'different_letters',
    'clipped_pair', 'explicit_reader_doubt', 'source_fallback',
    'failed_witness', 'conflicting_pair', 'weak_source', 'matching_pair_with_one_competitor',
])
def test_conserved_letter_groups_need_boundary_proof_not_a_new_spelling(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    # Independently written synthetic atoms. OCR confidence in an unusual
    # typeface does not choose any letter: both original inventories already
    # contain exactly the same letters, and two networks locate their word.
    source = words('This label R i v e r stays readable.')
    for i, item in enumerate(source[2:7]):
        item['bbox'] = [40 + i * 6, 20, 45 + i * 6, 32]
    for i, item in enumerate(source[7:]):
        item['bbox'] = [100 + i * 30, 20, 118 + i * 30, 32]
    word = dict(text='RIVER', bbox=[40, 20, 69, 32], confidence=47)
    witness = pa.CropWitness([*source[:2], word, *source[7:]])
    witness.model_reading = True
    pair = [[dict(word, confidence=63)], [dict(word, confidence=77)]]
    if evidence == 'missing_pair':
        pair = []
    elif evidence == 'weak_pair':
        pair[1][0]['confidence'] = 40
    elif evidence == 'different_letters':
        for view in pair:
            view[0]['text'] = 'RIMER'
    elif evidence == 'clipped_pair':
        for view in pair:
            view[0]['bbox'] = [52, 20, 69, 32]
    elif evidence == 'failed_witness':
        witness.failure = 'synthetic failed second read'
    elif evidence == 'conflicting_pair':
        pair += [[dict(word, text='RIMER', confidence=95)]] * 2
    elif evidence == 'weak_source':
        source[3]['confidence'] = 40
    elif evidence == 'matching_pair_with_one_competitor':
        pair += [[dict(word, text='LIVER', confidence=91)], [dict(word, text='RIVXR', confidence=17)]]
    witness.word_readings = lambda item: pair
    result = pa.adjudicate(
        'This label RIVER stays readable.'.split(), source, witness,
        uncertain={2} if evidence == 'explicit_reader_doubt' else (),
        unresolved_source=evidence == 'source_fallback',
    )
    live = [token for i, token in enumerate(result['tokens'])
            if not result['word_notes'].get(i, {}).get('source_image_only')]
    if evidence in ('complete_boundary', 'matching_pair_with_one_competitor'):
        assert live == 'This label RIVER stays readable.'.split()
        assert result['tokens'][2] == 'RIVER'
    else:
        assert 'RIVER' not in live, 'No spelling or missing boundary may be supplied by confidence alone'


@pytest.mark.parametrize('evidence', ['different_nonwords', 'different_nonword_lengths', 'label_space', 'missing_other', 'clipped_other', 'credible_word'])
def test_fragment_boundary_noise_cannot_choose_or_change_the_conserved_letters(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    # These abstract inventories are independently written for this test. Two
    # decoders locate one word but disagree on a letter; neither supplies text.
    source = words('This label H a r b o r stays readable.')
    for i, item in enumerate(source[2:8]):
        item['bbox'] = [40 + i * 6, 20, 45 + i * 6, 32]
    for i, item in enumerate(source[8:]):
        item['bbox'] = [110 + i * 30, 20, 128 + i * 30, 32]
    word = dict(text='HARBXR', bbox=[40, 20, 75, 32], confidence=92)
    witness = pa.CropWitness([*source[:2], word, *source[8:]])
    witness.model_reading = True
    pair = [[dict(word, confidence=59)], [dict(word, text='HARBKR', confidence=17)]]
    if evidence == 'different_nonword_lengths':
        pair[1][0]['text'] = 'HARBBKR'
    elif evidence == 'label_space':
        word.update(text=' HARBOR', confidence=2)
        pair = [[dict(word, text='HARBOR', confidence=91)]] * 2
    elif evidence == 'missing_other':
        pair[1] = []
    elif evidence == 'clipped_other':
        pair[1][0]['bbox'] = [55, 20, 75, 32]
    elif evidence == 'credible_word':
        pair[1][0].update(text='HARROW', confidence=91)
    witness.word_readings = lambda item: pair
    result = pa.adjudicate('This label HARBOR stays readable.'.split(), source, witness)
    live = [token for i, token in enumerate(result['tokens'])
            if not result['word_notes'].get(i, {}).get('source_image_only')]
    if evidence in ('different_nonwords', 'different_nonword_lengths', 'label_space'):
        expected = 'HARBOR' if evidence == 'label_space' else 'Harbor'
        assert live == f'This label {expected} stays readable.'.split()
        assert not any(t in result['tokens'] for t in ('HARBXR', 'HARBKR'))
        conflicts = pa.audit_agreement(result['tokens'], source, witness,
            word_boxes=result['boxes'], conserved_boundaries=result.get('conserved_boundaries'))
        assert 2 not in conflicts, 'A later sweep must keep the same proved letter conservation'
    else:
        assert 'HARBOR' not in live, 'Incomplete or credible competing evidence cannot supply a boundary'


@pytest.mark.parametrize('competing', [False, True])
def test_compilation_preserves_conserved_fragment_letters_through_both_sweeps(tmp_path, monkeypatch, competing):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp, pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def __init__(self): self.images = {}
        def image(self, name, data): self.images[name] = data
        def disclosure_image(self, name, display, rect):
            self.images[name] = display.pixmap(rect, scale=2, max_pixels=100000).tobytes('jpg')
    printed = 'Please keep this label Harbor beside the open window so every printed word remains readable.'
    with pymupdf.open() as drawing, pymupdf.open() as doc:
        leaf = drawing.new_page(width=640, height=150)
        leaf.insert_text((25, 60), printed, fontsize=9)
        whole = [dict(text=w[4], bbox=list(w[:4]), confidence=99) for w in leaf.get_text('words')]
        target = next(w for w in whole if w['text'] == 'Harbor')
        chars = [c for b in leaf.get_text('rawdict')['blocks'] for line in b.get('lines', [])
                 for span in line['spans'] for c in span['chars']
                 if target['bbox'][0] <= (c['bbox'][0] + c['bbox'][2]) / 2 <= target['bbox'][2]]
        source = []
        for word in whole:
            source.extend([dict(text=c['c'], bbox=list(c['bbox'])) for c in chars]
                          if word is target else [word])
        pixels = [dict(w, text='HARBXR', confidence=92) if w is target else w for w in whole]
        raster = leaf.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes('png')
        doc.new_page(width=640, height=150).insert_image(leaf.rect, stream=raster)
        assert not doc[0].get_text()
        def independent(*args):
            witness = pa.CropWitness(pixels)
            second = 'HARROW' if competing else 'HARBKR'
            witness.word_readings = lambda word: [[dict(target, text='HARBXR', confidence=59)],
                                                   [dict(target, text=second, confidence=91 if competing else 17)]]
            witness.alternates = lambda: [pixels, [dict(w, text=second) if w['text'] == 'HARBXR' else w for w in pixels]]
            return witness
        monkeypatch.setattr(pa, 'read_crop', independent)
        page, report = vp.compile_page({'blocks': [block(printed, bbox=(0, 0, 1000, 1000))]},
            source, SourceDisplay(doc, 0), 0, Package(), {}, pixel_words=source,
            note_context={'cache': tmp_path/'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    own = report['blocks'][0]
    body = vp._seam_text(' '.join(page['body']))
    if competing:
        assert 'Harbor' not in body and own['source_image_words']==[]
        from tests.unit.test_reflow_reading_tiers import assert_flagged_print
        assert_flagged_print(''.join(page['body']), list('Harbor'))
        assert 'HARBXR'not in body and 'HARROW'not in body
        assert any(d['status']=='UNRESOLVED'for d in own['adjudication'])
    else:
        assert body == printed
        assert own['source_image_words'] == []
        assert 'HARBXR' not in body and 'HARBKR' not in body


@pytest.mark.parametrize('evidence', ['paired_terminal_noise', 'boundary_terminal_noise',
    'different_word', 'internal_dot', 'lost_possessive', 'missing_pair', 'weak_pair', 'clipped_pair'])
def test_conserved_word_boundaries_do_not_choose_recognizer_terminal_punctuation(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    # All words and layouts below are written for this test. The existing
    # inventories agree exactly; the independent views may locate a word
    # without being allowed to add their own terminal dot or drop a comma.
    source = words('This label H a r b o r , stays readable.')
    for i, item in enumerate(source[2:9]):
        item['bbox'] = [40 + i * 6, 20, 45 + i * 6, 32]
    for i, item in enumerate(source[9:]):
        item['bbox'] = [120 + i * 30, 20, 138 + i * 30, 32]
    word = dict(text='HARBXR,.', bbox=[40, 20, 81, 32], confidence=47)
    witness = pa.CropWitness([*source[:2], word, *source[9:]])
    witness.model_reading = True
    pair = [[dict(word, text='HARBOR,', confidence=71)],
            [dict(word, text='HARBOR,.', confidence=78)]]
    if evidence == 'boundary_terminal_noise':
        pair[0][0]['text'] = 'HARBXR,'
        pair[1][0]['text'] = 'HARBKR,.'
    elif evidence == 'different_word':
        for view in pair: view[0]['text'] = 'HARROW,.'
    elif evidence == 'internal_dot':
        for view in pair: view[0]['text'] = 'HAR.BOR,'
    elif evidence == 'lost_possessive':
        for view in pair: view[0]['text'] = "HARBOR'S,"
    elif evidence == 'missing_pair':
        pair[1] = []
    elif evidence == 'weak_pair':
        pair[1][0]['confidence'] = 40
    elif evidence == 'clipped_pair':
        pair[1][0]['bbox'] = [60, 20, 81, 32]
    witness.word_readings = lambda item: pair
    result = pa.adjudicate('This label HARBOR, stays readable.'.split(), source, witness)
    live = [token for i, token in enumerate(result['tokens'])
            if not result['word_notes'].get(i, {}).get('source_image_only')]
    if evidence in ('paired_terminal_noise', 'boundary_terminal_noise'):
        expected = 'HARBOR,' if evidence == 'paired_terminal_noise' else 'Harbor,'
        assert live == f'This label {expected} stays readable.'.split()
        assert 'HARBOR,.' not in live
    else:
        assert 'HARBOR,' not in live


@pytest.mark.parametrize('evidence', ['no_space', 'unknown_space', 'printed_space', 'credible_word', 'clipped_pair', 'explicit_doubt'])
def test_measured_unbroken_ink_can_locate_conserved_fragment_letters(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source = words('This label H a r b o r stays readable.')
    for i, item in enumerate(source[2:8]):
        item['bbox'] = [40 + i * 6, 20, 45 + i * 6, 32]
    for i, item in enumerate(source[8:]):
        item['bbox'] = [110 + i * 30, 20, 128 + i * 30, 32]
    word = dict(text='HARBXR', bbox=[40, 20, 75, 32], confidence=47)
    witness = pa.CropWitness([*source[:2], word, *source[8:]])
    witness.model_reading = True
    pair = [[dict(word, text='HARBKR', confidence=71)]] * 2
    if evidence == 'credible_word':
        pair = [[dict(word, text='HARROW', confidence=91)]] * 2
    elif evidence == 'clipped_pair':
        pair = [[dict(word, text='HARBKR', bbox=[55, 20, 75, 32], confidence=71)]] * 2
    witness.word_readings = lambda item: pair
    witness.fragment_boundary = lambda item: {'unknown_space': None, 'printed_space': False}.get(evidence, True)
    result = pa.adjudicate('This label HARBOR stays readable.'.split(), source, witness,
                          uncertain={2} if evidence == 'explicit_doubt' else ())
    live = [token for i, token in enumerate(result['tokens'])
            if not result['word_notes'].get(i, {}).get('source_image_only')]
    if evidence == 'no_space':
        assert live == 'This label Harbor stays readable.'.split()
        assert not result['marked']
    else:
        assert 'Harbor' not in live and 'HARBOR' not in live


@pytest.mark.parametrize('layout', ['tight', 'space', 'blank', 'overflow'])
def test_fragment_boundary_measures_pixels_without_recognizing_the_spelling(layout, tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=100)
        if layout != 'blank':
            # The fixed synthetic letters and large intervening space are
            # authored here. No OCR answer is passed to this pixel measure.
            page.insert_text((25, 50), 'RIV ER' if layout == 'space' else 'RIVER', fontsize=12)
            boxes = page.get_text('words')
            bounds = [min(w[0] for w in boxes), min(w[1] for w in boxes),
                      max(w[2] for w in boxes), max(w[3] for w in boxes)]
        else: bounds = [25, 35, 70, 52]
        if layout == 'overflow': bounds = [-10, 35, 70, 52]
        measure = getattr(pa, '_fragment_word_boundary', lambda *args: None)
        display = SourceDisplay(doc, 0)
        result = measure(display, dict(bbox=bounds))
        monkeypatch.setattr(pa, '_read_crop', lambda *args, **kwargs: [])
        witness = pa.read_crop(display, [bounds], tmp_path/'cache')
        terminal = pa._terminal_word_view([], witness)
        assert terminal.fragment_boundary(dict(bbox=bounds)) is result
    assert result is (True if layout == 'tight' else None if layout in ('blank', 'overflow') else False)


@pytest.mark.parametrize('evidence', ['complete_original', 'printed_space', 'unknown_space', 'clipped_own_views', 'weak_original', 'explicit_doubt', 'different_letters', 'other_row'])
def test_clipped_context_cannot_hide_a_conserved_word_from_its_complete_own_crop(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source = words('This label H a r b o r stays readable.')
    for i, item in enumerate(source[2:8]):
        item['bbox'] = [40 + i * 6, 20, 45 + i * 6, 32]
    for i, item in enumerate(source[8:]):
        item['bbox'] = [110 + i * 30, 20, 128 + i * 30, 32]
    word = dict(text='HARBOR', bbox=[40, 20, 75, 32], confidence=94)
    # The contextual recognizer discarded the prefix before the lazy word
    # query. Its tail rectangle cannot bound the complete independent crop.
    clipped = dict(word, bbox=[53, 20, 75, 32])
    witness = pa.CropWitness([*source[:2], clipped, *source[8:]])
    witness.model_reading = True
    pair = [[dict(word, confidence=91)]] * 2
    if evidence == 'clipped_own_views': pair = [[clipped]] * 2
    elif evidence == 'weak_original': source[3]['confidence'] = 40
    elif evidence == 'different_letters':
        for item in pair: item[0] = dict(word, text='HARROW', confidence=91)
    elif evidence == 'other_row': source[4]['bbox'] = [52, 45, 57, 57]
    calls = []
    def own(item):
        calls.append(item['bbox'])
        return pair
    witness.word_readings = own
    witness.fragment_boundary = lambda item: {'printed_space': False, 'unknown_space': None}.get(evidence, True)
    result = pa.adjudicate('This label HARBOR stays readable.'.split(), source, witness,
                          uncertain={2} if evidence == 'explicit_doubt' else ())
    live = [token for i, token in enumerate(result['tokens'])
            if not result['word_notes'].get(i, {}).get('source_image_only')]
    if evidence == 'complete_original':
        assert live == 'This label Harbor stays readable.'.split()
        assert [40, 20, 75, 32] in calls, 'The original inventory bounds the complete own-word read'
        assert not result['marked']
    else:
        assert 'HARBOR' not in live and 'Harbor' not in live


@pytest.mark.parametrize('weak_glyph', ["'", '|', '.'])
def test_unproved_lexical_edit_cannot_publish_weak_ocr_punctuation_as_a_word(weak_glyph):
    from cps.services.reflow import pixel_adjudication as pa
    # The synthetic scan prints Harbor. The source recognizer split its first
    # stroke into a punctuation guess; neither guess is a proven live word.
    source = words('Read the ' + weak_glyph + ' arbor sample now.', 40)
    witness = pa.CropWitness()
    witness.model_reading = True
    result = pa.adjudicate('Read the Harbor sample now.'.split(), source, witness)
    index = result['tokens'].index(weak_glyph)
    assert result['word_notes'][index]['source_image_only']
    assert result['word_notes'][index]['source'] == [weak_glyph]
    assert result['word_notes'][index]['source_bbox'] == source[2]['bbox']
    assert result['word_notes'][index]['status'] == 'UNRESOLVED'
    assert index in result['marked']


def test_proved_independent_punctuation_is_not_hidden_by_an_unrelated_word_edit():
    from cps.services.reflow import pixel_adjudication as pa
    source = words('Read | the clcar sample now.')
    witness = pa.CropWitness(words('Read | the clear sample now.'))
    witness.model_reading = True
    result = pa.adjudicate('Read | the clear sample now.'.split(), source, witness)
    assert result['tokens'][1] == '|'
    assert 1 not in result['marked']


@pytest.mark.parametrize('evidence', ['missing', 'weak_other_glyph', 'matching_pixels', 'visible_native'])
def test_source_only_mixed_numeric_glyphs_need_pixel_proof_before_becoming_live(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    # The numeral is independently authored. Ambiguous OCR is not a printed
    # identifier merely because its own confidence is high. The matching-pixel
    # control deliberately prints the literal mixed identifier instead.
    source = words('Read entry 7l now.')
    witness = pa.CropWitness([])
    if evidence == 'weak_other_glyph':
        witness.extend(words('Read entry 71 now.', 40))
    elif evidence == 'matching_pixels':
        witness.extend(words('Read entry 7l now.'))
    witness.model_reading = True
    result = pa.adjudicate('Read entry now.'.split(), source, witness,
                          source_print=evidence == 'visible_native')
    index = result['tokens'].index('7l')
    if evidence in ('missing', 'weak_other_glyph'):
        assert result['word_notes'][index]['source_image_only']
        assert result['word_notes'][index]['source_bbox'] == source[2]['bbox']
        assert result['word_notes'][index]['status'] == 'UNRESOLVED'
    else:
        assert not result['word_notes'].get(index, {}).get('source_image_only')
        assert result['word_notes'][index]['status'] == 'RESOLVED'


def test_source_only_ordinary_number_keeps_existing_conservation_contract():
    from cps.services.reflow import pixel_adjudication as pa
    source = words('Read entry 71 now.')
    witness = pa.CropWitness([]); witness.model_reading = True
    result = pa.adjudicate('Read entry now.'.split(), source, witness)
    index = result['tokens'].index('71')
    assert not result['word_notes'][index].get('source_image_only')


@pytest.mark.parametrize('obstruction',['none','other_owner','different_text'])
def test_clipped_final_row_uses_unclaimed_complete_optical_territory(tmp_path,monkeypatch,obstruction):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    text='The sample keeps every clear word close to the end today.'
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        page=drawing.new_page(width=400,height=160)
        page.insert_text((30,80),text,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        doc.new_page(width=400,height=160).insert_image(page.rect,stream=page.get_pixmap().tobytes('png'))
        display=SourceDisplay(doc,0)
        own=block(text if obstruction!='different_text'else text.replace('today.','tomorrow.'),ident='p1',bbox=(50,350,950,600))
        clip=vp.vision_reading.union([w['bbox']for w in source[:7]])
        regions={'p1':[clip],'_instances':{vp._source_instance(w):'p1'for w in source[:7]}}
        answer={'blocks':[own]}
        if obstruction=='other_owner':
            other=block('to the end today.',ident='p2',bbox=(600,350,950,600))
            answer['blocks'].append(other)
            regions['p2']=[vp.vision_reading.union([w['bbox']for w in source[7:]])]
            regions['_instances'].update({vp._source_instance(w):'p2'for w in source[7:]})
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness([
            dict(w)for w in source if any(vp.owned_words([w],b)for b in boxes)]))
        _,report=vp.compile_page(answer,[],display,0,Package(),{},pixel_words=source,regions=regions,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=next(b for b in report['blocks']if b['id']=='p1')
        extended=any(g['kind']=='complete_optical_row_territory'for g in record['guard'])
        if obstruction=='none':
            assert record['published_tokens']==text.split()
            assert not record['marked'],'A clipped evidence strip cannot manufacture doubt on its complete printed tail'
            assert extended
        else:assert not extended,'A differing or separately owned tail cannot be borrowed'


def test_unsettled_recovered_nonword_cannot_remain_a_live_transcription():
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The prlnted sample stays readable.',confidence=80)
    witness=pa.CropWitness(words('The prlnted sample stays readable.',confidence=75))
    witness.model_reading=True
    witness.word_readings=lambda word:[words('The printed sample stays readable.',confidence=30)]*2
    result=pa.adjudicate([w['text']for w in source],source,witness,uncertain={1},unresolved_source=True)
    assert result['tokens'][1]=='prlnted','Unsettled ink must not be guessed from the dictionary'
    assert result['word_notes'][1].get('source_image_only'),'A weak recovered nonword needs original ink, not wrong live text'
    assert result['word_notes'][1]['source_bbox']==source[1]['bbox']


@pytest.mark.parametrize('credible',['single_network_noise','paired_nonword','real_word','explicit_doubt'])
def test_complete_optical_counterreadings_remain_evidence_even_for_uncommon_words(credible):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clcar sample stays readable.',confidence=96)
    witness=pa.CropWitness(words('The clcar sample stays readable.',confidence=96))
    witness.model_reading=True
    if credible=='real_word':
        source[1]['text']='clean';witness[1]['text']='clean'
    witness.word_readings=lambda word:[
        words('The '+('clean'if credible=='real_word'else'clcar')+' sample stays readable.',confidence=96),
        words('The '+('clean'if credible=='real_word'else'clcar')+' sample stays readable.',confidence=96 if credible=='paired_nonword'else 30)]
    result=pa.adjudicate('The clear sample stays readable.'.split(),source,witness,
        uncertain={1}if credible=='explicit_doubt'else())
    note=result['word_notes'][1]
    assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert result['marked']=={1},'An uncommon complete counterreading is not disposable noise'


@pytest.mark.parametrize('context',['complete','clipped','different_word','conflicting_pairs','explicit_doubt'])
def test_complete_context_can_corroborate_a_model_without_certifying_a_shared_stroke_guess(context):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    with pymupdf.open()as doc:
        page=doc.new_page();page.insert_text((30,70),'A modern sample remains readable.',fontsize=12)
        assert 'modern'in page.get_text().split()
    source=words('A modem sample remains readable.')
    witness=pa.CropWitness(words('A modem sample remains readable.'));witness.model_reading=True
    witness.word_readings=lambda word:[words('A modem sample remains readable.')]*2
    def contexts(word):
        view=words('A '+('modem'if context=='different_word'else'modern')+' sample remains readable.')
        if context=='clipped':view[1]['bbox']=[27,20,38,32]
        return [view,view]+([words('A modem sample remains readable.')]*2 if context=='conflicting_pairs'else[])
    witness.word_context_readings=contexts
    result=pa.adjudicate('A modern sample remains readable.'.split(),source,witness,
        uncertain={1}if context=='explicit_doubt'else())
    note=result['word_notes'][1]
    if context=='complete':
        assert result['tokens'][1]=='modern'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='contextual_pixel_corroboration_of_model'
    else:assert note['status']=='UNRESOLVED'and note.get('source_image_only')


@pytest.mark.parametrize('glyph',['horizontal','plus','two_marks','unavailable'])
def test_horizontal_punctuation_needs_its_own_pixel_shape_not_word_recognition(glyph):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=100,height=70)
        page.draw_line((30,35),(45,35),width=1)
        if glyph=='plus':page.draw_line((37,27),(37,43),width=1)
        if glyph=='two_marks':page.draw_line((30,40),(45,40),width=1)
        display=SourceDisplay(doc,0)
        source=[dict(text='—',bbox=[28,25,47,45],confidence=70)]
        witness=pa.CropWitness();witness.model_reading=True
        witness.dash_shape=lambda word:pa._horizontal_dash_shape(display,word)if glyph!='unavailable'else False
        result=pa.adjudicate(['–'],source,witness)
        if glyph=='horizontal':
            assert result['tokens']==['–']and not result['marked']
            assert not result['decisions'],'Equivalent dash style adds no word or lexical correction'
        else:assert result['marked'],'A different or unavailable shape cannot certify punctuation'


@pytest.mark.parametrize('proof',['spaces','unknown_gap','different_letters'])
def test_unproved_prefix_cannot_withhold_conserved_words_after_proved_source_spaces(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='blurrysamplewordsremain.',bbox=[0,20,98,32],confidence=80)]
    optical=[dict(text=t,bbox=[x,20,x+w,32],confidence=c)for t,x,w,c in(
        ('blurry',0,25,80),('sample',30,25,99),('words',60,18,99),('remain.',83,15,99))]
    if proof=='different_letters':optical[2]['text']='swords'
    witness=pa.CropWitness(optical);witness.model_reading=True
    witness.word_gap=lambda left,right:proof!='unknown_gap'
    result=pa.adjudicate('Unclear sample words remain.'.split(),source,witness)
    if proof=='spaces':
        assert result['tokens'][-3:]==['sample','words','remain.']
        assert not set(range(1,4))&result['marked'],'An unresolved prefix owns no doubt in the conserved following words'
        assert 0 in result['marked'],'The unresolved lexical difference still has its own source control'
    else:assert result['tokens'][-3:]!=['sample','words','remain.'],'Unproved spacing or changed letters cannot split this source instance'


@pytest.mark.parametrize('proof',['missing','independent','real_model'])
def test_recovered_common_word_is_not_verified_by_dictionary_membership(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read this part now.',confidence=75)
    witness=pa.CropWitness(words('Read this part now.',confidence=96 if proof=='independent'else 70))
    witness.model_reading=True
    result=pa.adjudicate([w['text']for w in source],source,witness,uncertain={2},
        unresolved_source=proof!='real_model')
    if proof=='missing':assert result['word_notes'][2].get('source_image_only')
    elif proof=='independent':assert 2 not in result['marked']
    else:assert not result['word_notes'][2].get('source_image_only')


def test_a_complete_uncommon_printed_word_cannot_be_rejected_by_the_dictionary():
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    with pymupdf.open()as doc:
        page=doc.new_page();page.insert_text((30,70),'The prlnted sample stays readable.',fontsize=12)
        assert 'prlnted'in page.get_text().split()
    source=words('The prlnted sample stays readable.',confidence=80)
    witness=pa.CropWitness(words('The prlnted sample stays readable.',confidence=99))
    witness.model_reading=True
    witness.word_readings=lambda word:[]
    result=pa.adjudicate('The printed sample stays readable.'.split(),source,witness)
    note=result['word_notes'][1]
    assert note['status']=='UNRESOLVED','A complete printed alternative is evidence even if it is uncommon'
    assert note.get('source_image_only'),'A dictionary preference cannot publish the differing model word'
    assert result['marked']=={1}


@pytest.mark.parametrize('obstruction',['none','other_owner','different_text','optical_spelling','different_native_word'])
def test_clipped_middle_row_recovers_its_printed_tail_beyond_a_bad_model_hint(tmp_path,monkeypatch,obstruction):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    first='The sample keeps every clear word close to the end today.'
    second='A short final row follows.'
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        page=drawing.new_page(width=400,height=160)
        page.insert_text((30,70),first,fontsize=10)
        page.insert_text((30,88),second,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        doc.new_page(width=400,height=160).insert_image(page.rect,stream=page.get_pixmap().tobytes('png'))
        display=SourceDisplay(doc,0)
        text=first+' '+second
        own=block(text if obstruction!='different_text'else text.replace('today.','tomorrow.'),ident='p1',bbox=(50,300,500,700))
        kept=source[:7]+source[len(first.split()):]
        regions={'p1':[vp.vision_reading.union([w['bbox']for w in source[:7]]),vp.vision_reading.union([w['bbox']for w in source[len(first.split()):]])],
            '_instances':{vp._source_instance(w):'p1'for w in kept}}
        answer={'blocks':[own]}
        if obstruction=='other_owner':
            other=block('to the end today.',ident='p2',bbox=(500,300,950,500));answer['blocks'].append(other)
            regions['p2']=[vp.vision_reading.union([w['bbox']for w in source[7:len(first.split())]])]
            regions['_instances'].update({vp._source_instance(w):'p2'for w in source[7:len(first.split())]})
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness([
            dict(w)for w in source if any(vp.owned_words([w],b)for b in boxes)]))
        import copy
        optical=copy.deepcopy(source);original=[]
        if obstruction in ('optical_spelling','different_native_word'):
            original=copy.deepcopy(source);optical[7]['text']='te'
            if obstruction=='different_native_word':original[7]['text']='te'
        _,report=vp.compile_page(answer,original,display,0,Package(),{},pixel_words=optical,regions=regions,
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=next(b for b in report['blocks']if b['id']=='p1')
        extended=any(g['kind']=='complete_optical_row_territory'for g in record['guard'])
        if obstruction in ('none','optical_spelling'):
            assert record['published_tokens']==text.split()
            assert not record['marked'],'The row after a clipped row must not hide its missing printed words'
            assert extended
        else:assert not extended,'A differing or separately owned tail cannot be borrowed'


@pytest.mark.parametrize('ink',['absent','end_of_row','intervening_row','raised_end','lower_start'])
def test_model_insertion_across_a_wrap_requires_both_original_empty_margins(tmp_path,monkeypatch,ink):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    class Package:
        def image(self,*args):pass
        def disclosure_image(self,*args):pass
    first='The sample keeps every word in the'
    second='example readable beside the window.'
    with pymupdf.open()as drawing,pymupdf.open()as doc:
        page=drawing.new_page(width=400,height=180)
        page.insert_text((30,70),first,fontsize=10)
        if ink=='end_of_row':page.insert_text((190,70),'sixth',fontsize=10)
        if ink=='raised_end':page.insert_text((190,57),'sixth',fontsize=7)
        if ink=='lower_start':page.insert_text((10,101),'sixth',fontsize=7)
        if ink=='intervening_row':page.insert_text((30,88),'sixth',fontsize=10)
        page.insert_text((30,106 if ink=='intervening_row'else 88),second,fontsize=10)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')if w[4]!='sixth']
        doc.new_page(width=400,height=180).insert_image(page.rect,stream=page.get_pixmap().tobytes('png'))
        display=SourceDisplay(doc,0)
        monkeypatch.setattr(pa,'read_crop',lambda display,boxes,cache:pa.CropWitness(source))
        own=block(first+' sixth '+second,ident='p1',bbox=(0,0,1000,1000))
        _,report=vp.compile_page({'blocks':[own]},[],display,0,Package(),{},pixel_words=source,
            regions={'p1':[[0,0,400,180]]},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        record=report['blocks'][0]
        if ink=='absent':
            assert record['published_tokens']==(first+' '+second).split(), 'Empty wrap margins cannot publish an invented word'
            assert any(d['model']==['sixth']and not d['chosen']and d['status']=='RESOLVED'
                for d in record['adjudication'])
        else:assert 'sixth'in record['published_tokens'], 'Original ink or an intervening row cannot prove deletion'


@pytest.mark.parametrize('opening',['complete','mixed_metrics','other_owner','different_text','not_column_edge'])
def test_compact_body_proof_cannot_discard_its_complete_previous_column_opening(opening):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from tests.unit.test_reflow_vision_pilot import block
    first='Quiet visitors walk beside the wide garden gate and carry their small bags home before sunset'
    rest=('The next printed column keeps this complete paragraph readable while ordinary workers prepare tools for tomorrow. '
          'Fresh panels stand beside the window and several boxes wait near the wooden table. '
          'These separate sentences supply enough source anchors for the complete passage to keep its own location.')
    optical=[]
    for text,x,y in [(first,20,330 if opening!='not_column_edge'else 160),(rest,220,40)]:
        for i,t in enumerate(text.split()):
            xx=x+(i%5)*31;yy=y+(i//5)*13
            optical.append(dict(text=t,bbox=[xx,yy,xx+27,yy+9],confidence=99))
    if opening=='mixed_metrics':optical[2]['bbox'][1]+=5
    # One recognition error inside the long continuation must not make its
    # compact suffix window erase a separately complete source opening.
    optical[len(first.split())+19]['text']='wind0w'
    own=block((first if opening!='different_text'else first.replace('Quiet','Noisy'))+' '+rest,
        ident='p1',bbox=(520,80,990,700))
    answer={'blocks':[own]}
    if opening=='other_owner':answer['blocks'].insert(0,block(first,ident='p0',bbox=(0,800,470,990)))
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,400,400));regions={}
    bound,_=vp.bind_geometry(answer,optical,display,regions)
    local=vp._owned_block_words(next(b for b in bound['blocks']if b['id']=='p1'),bound,
        optical,display,regions,ordered=True)
    got=[w['text']for w in local]
    if opening in ('complete','mixed_metrics'):
        assert got[:len(first.split())]==first.split(), 'A complete earlier-column opening must not become a separate fallback unit'
        assert len(regions['p1'])>=2
    else:assert got[:len(first.split())]!=first.split(),'Unproved or separately owned words cannot be taken as a column opening'


@pytest.mark.parametrize('evidence',['complete','missing','clipped','different','conflicting','counterreading','unknown_word'])
def test_recovered_word_doubt_can_be_settled_by_complete_independent_context(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read this part beside the open window.',confidence=72)
    witness=pa.CropWitness(words('Read this part beside the open window.',confidence=65))
    views=[words('Read this part beside the open window.',confidence=97)]*2
    if evidence=='missing':views=[]
    if evidence=='clipped':
        import copy
        views=copy.deepcopy(views)
        for view in views:view[2]['bbox']=[50,20,58,32]
    if evidence=='different':views=[words('Read this path beside the open window.',confidence=97)]*2
    if evidence=='conflicting':views +=[words('Read this path beside the open window.',confidence=97)]*2
    if evidence=='counterreading':witness[2].update(text='path',confidence=97)
    if evidence=='unknown_word':
        import copy
        views=copy.deepcopy(views);source[2]['text']='prlnted';witness[2]['text']='prlnted'
        for view in views:view[2]['text']='prlnted'
    witness.word_context_readings=lambda word:views
    result=pa.adjudicate([w['text']for w in source],source,witness,uncertain={2},unresolved_source=True)
    assert result['tokens']==[w['text']for w in source]
    if evidence=='complete':assert 2 not in result['marked'],'Independent complete context settles this own occurrence, not dictionary familiarity'
    else:assert result['word_notes'][2].get('source_image_only'),'Missing, partial or conflicting recognition cannot certify the local guess'


@pytest.mark.parametrize('proof',['whole','missing','different','still_clipped','competing','neighbor_word'])
def test_clipped_word_hint_is_reread_on_its_complete_measured_ink(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('This clear sample remains readable.')
    source[1].update(text='clcar',bbox=[27,20,38,32])
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    whole=dict(text='clear',bbox=[20,20,38,32],confidence=99)
    witness.word_scope=lambda word:[20,20,38,32]
    def readings(word):
        if word['bbox'][0]>20:return [[dict(source[1])]]*2
        if proof=='missing':return []
        value=dict(whole)
        if proof=='different':value['text']='clean'
        if proof=='still_clipped':value['bbox']=[27,20,38,32]
        views=[[value],[dict(value)]]
        if proof=='competing':views +=[[dict(whole,text='clean')]]*2
        return views
    if proof=='neighbor_word':witness.insert(1,dict(text='cl',bbox=[20,20,24,32],confidence=99))
    witness.word_readings=readings
    result=pa.adjudicate('This clear sample remains readable.'.split(),source,witness)
    note=result['word_notes'][1]
    if proof in('whole','missing','still_clipped'):
        assert result['tokens'][1]=='clear'and note['status']=='RESOLVED'
        assert not note.get('source_image_only'),'A missing or clipped reread is no complete counterevidence to the certain first reading'
        assert result['boxes'][1]==whole['bbox']
    else:assert note['status']=='UNRESOLVED'and note.get('source_image_only')


@pytest.mark.parametrize('shape',['diagram','two_rows','words','wide','no_colon'])
def test_missing_prose_cannot_turn_a_compact_trailing_diagram_into_ocr_words(shape):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    optical=[]
    for line,text in enumerate(['This synthetic paragraph remains ordinary prose here.',
                               'Its separate printed words keep their own places.',
                               'Read the following simple drawing carefully:']):
        for i,token in enumerate(text.split()):
            optical.append(dict(text=token,bbox=[10+i*30,10+line*15,37+i*30,20+line*15],confidence=99))
    if shape=='no_colon':optical[-1]['text']='carefully.'
    for line in range(2 if shape=='two_rows'else 4):
        values=['one','two']if shape=='words'else ['9','>)']
        for i,token in enumerate(values):
            x=15+i*180 if shape=='wide'else 100+i*20
            optical.append(dict(text=token,bbox=[x,60+line*15,x+8,70+line*15],confidence=98))
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,260,150))
    # cover_missing_lines accepts the same typed page envelope used by callers.
    answer=dict(page=0,blocks=[])
    result,recovered=vp.cover_missing_lines(answer,optical,[],display,{},{} )
    figures=[b for b in result['blocks']if b['role']=='figure']
    if shape=='diagram':
        assert len(figures)==1,'A compact nonprose drawing is original artwork, not digit-shaped OCR words'
        prose=[b for b in result['blocks']if b['role']!='figure']
        assert ' '.join(b['text']for b in prose)==' '.join(w['text']for w in optical[:-8])
        assert all('>)'not in b['text']for b in result['blocks'])
        assert vp.block_rect(figures[0],display)==pytest.approx([100,60,128,115],abs=1e-9)
        assert figures[0]['id']in recovered
    else:
        assert not figures,'Insufficient or prose-shaped evidence must not reclassify text'
        assert ' '.join(b['text']for b in result['blocks'])==' '.join(w['text']for w in optical)


@pytest.mark.parametrize('proof',['missing','different','exact'])
def test_weak_recovered_punctuation_needs_its_own_printed_identity(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read >) here.');source[1]['confidence']=40
    witness=pa.CropWitness(words('Read >) here.'))
    if proof=='missing':witness.pop(1)
    elif proof=='different':witness[1].update(text='→',confidence=50)
    result=pa.adjudicate('Read >) here.'.split(),source,witness,uncertain={1},unresolved_source=True)
    assert result['tokens']==['Read','>)','here.']
    if proof=='exact':assert 1 not in result['marked']
    else:assert result['word_notes'][1].get('source_image_only'),'Weak punctuation can be a misread graphical glyph; disclose only that own occurrence'


@pytest.mark.parametrize('layout',['new_line','split_line'])
@pytest.mark.parametrize('obstruction',['none','other_owner','different','distant','intervening'])
def test_clipped_model_tail_can_own_its_exact_adjacent_next_printed_line(obstruction,layout):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    optical=[]
    lines=['The garden visitors can read this notice',
           'and keep every complete printed word near',
           'the window.']
    for row,text in enumerate(lines):
        for col,token in enumerate(text.split()):
            optical.append(dict(text=token,bbox=[10+col*25,20+row*15,33+col*25,30+row*15],confidence=99))
    tail=optical[-2:]
    if layout=='split_line':tail[0]['bbox']=[195,35,218,45]
    if obstruction=='different':tail[-1]['text']='door.'
    if obstruction=='distant':
        for w in tail:w['bbox'][1]+=70;w['bbox'][3]+=70
    if obstruction=='intervening':optical.insert(-2,dict(text='Other',bbox=[10,46,33,51],confidence=99))
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,260,160))
    block=dict(id='sample',role='body',text=' '.join(lines),bbox=[0,100,950,300])
    answer=dict(blocks=[block]);regions={'sample':[[5,18,195,46]]};owners={}
    if obstruction=='other_owner':regions['_instances']={vp._source_instance(w):'foreign'for w in tail}
    got=vp._complete_optical_row_territory(block,answer,optical,display,regions,owners)
    assert got==(tail if obstruction=='none'else[]),'Only the adjacent uniquely matched unclaimed printed tail belongs to this reading'


@pytest.mark.parametrize('evidence',['complete','unmeasured','foreign_word','different','conflicting_pairs'])
def test_contextual_word_reads_include_the_complete_measured_original_ink(monkeypatch,tmp_path,evidence):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    source=dict(text='clcar',bbox=[30,30,45,40],confidence=97)
    complete=[25,25,45,40]
    frames=[]
    def recognize(display,regions,cache,**kwargs):
        if kwargs.get('psm')!=7:return [dict(source)]
        frames.append(regions[0])
        intact=regions[0][0]<=25 and regions[0][1]<=25
        token='clear'if intact else'clcar'
        if evidence=='different'or evidence=='conflicting_pairs'and kwargs.get('scale')==4:token='clean'
        return [dict(text=token,bbox=list(complete if intact else source['bbox']),confidence=99)]
    monkeypatch.setattr(pa,'_read_crop',recognize)
    monkeypatch.setattr(pa,'_latin_directory',lambda engine:tmp_path)
    monkeypatch.setattr(pa,'_word_ink_scope',lambda *a,**k:None if evidence=='unmeasured'else complete)
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,100,80))
    witness=pa.read_crop(display,[source['bbox']],tmp_path)
    if evidence=='foreign_word':witness.append(dict(text='a',bbox=[25,25,29,40],confidence=99))
    chosen=pa._context_model_choice(source,witness,'clear')
    if evidence=='complete':
        assert chosen is not None and chosen['text']=='clear'
        assert chosen['bbox']==complete
        assert all(frame[1]<=25 and frame[3]>=40 for frame in frames),'A contextual reread cannot discard the measured ascender or prefix'
    else:assert chosen is None,'Missing geometry, another word or conflicting complete readings cannot certify the model'


@pytest.mark.parametrize('evidence',[
    'clipped_prefix','clipped_vertical','unmeasured','contained','foreign_word',
    'complete_competitor','complete_uncommon_competitor','explicit_doubt',
    'source_fallback','failed_witness','different_occurrence','complete_source','baseline_owner'])
def test_positive_missing_ink_defeats_optical_confidence_not_the_complete_model(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('This clcar sample remains readable.')
    source[1]['bbox']=[20,24,38,30]if evidence=='clipped_vertical'else[27,20,38,32]
    complete=[20,20,38,32]
    if evidence=='complete_source':source[1]['bbox']=list(complete)
    if evidence=='baseline_owner':source[0]['bbox']=[20,20,24,32]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    def read(word):
        view=[dict(w)for w in source]
        if evidence in('complete_competitor','complete_uncommon_competitor'):
            view[1]=dict(text='clean'if evidence=='complete_competitor'else'prlnted',bbox=complete,confidence=99)
        return [view,[dict(w)for w in view]]
    witness.word_readings=read
    witness.word_scope=lambda word:(None if evidence=='unmeasured'else
        list(source[1]['bbox'])if evidence=='contained'else
        [0,20,18,32]if evidence=='different_occurrence'else complete)
    if evidence=='foreign_word':witness.insert(1,dict(text='a',bbox=[20,20,24,32],confidence=99))
    if evidence=='failed_witness':witness.failure='recognition unavailable';witness.model_reading=False
    result=pa.adjudicate('This clear sample remains readable.'.split(),source,witness,
        uncertain={1}if evidence=='explicit_doubt'else(),unresolved_source=evidence=='source_fallback')
    note=result['word_notes'][1]
    if evidence in('clipped_prefix','clipped_vertical'):
        assert result['tokens'][1]=='clear'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
        assert result['boxes'][1]==complete,'The retained reading still owns all measured original ink'
    else:
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert result['marked']=={1},'Incomplete recognition cannot weaken a complete competing word, explicit doubt or source fallback'


@pytest.mark.parametrize('proof',['unmeasured','measured','neighbor_limit','other_word','conflicting_networks','missing_network'])
def test_contextual_reread_does_not_reuse_a_recognizers_clipped_vertical_frame(monkeypatch,tmp_path,proof):
    import pymupdf
    from types import SimpleNamespace
    from cps.services.reflow import pixel_adjudication as pa
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=200,height=100)
        page.insert_text((40,50),'clear',fontsize=14)
        printed=page.get_text('words')[0]
        assert printed[4]=='clear'
        full=list(printed[:4])
    height=full[3]-full[1]
    cropped=[full[0],full[1]+height*.25,full[2],full[3]-height*.15]
    source=dict(text='clcar',bbox=cropped,confidence=99)
    frames=[]
    def recognize(display,regions,cache,**kwargs):
        if kwargs.get('psm')!=7:return [dict(source)]
        frames.append(regions[0])
        if proof=='missing_network'and kwargs.get('language')=='LatinRaw':return []
        intact=regions[0][1]<=full[1]and regions[0][3]>=full[3]
        token='clear'if intact else'clcar'
        if proof=='other_word'or proof=='conflicting_networks'and kwargs.get('scale')==4:token='clean'
        return [dict(text=token,bbox=full if intact else cropped,confidence=99)]
    monkeypatch.setattr(pa,'_read_crop',recognize)
    monkeypatch.setattr(pa,'_latin_directory',lambda engine:tmp_path)
    monkeypatch.setattr(pa,'_word_ink_scope',lambda *a,**k:full if proof=='measured'else None)
    if proof=='neighbor_limit':monkeypatch.setattr(pa,'_neighbor_line_limits',lambda *a:(cropped[1],cropped[3]))
    witness=pa.read_crop(SimpleNamespace(rect=pymupdf.Rect(0,0,200,100)),[cropped],tmp_path)
    choice=pa._context_model_choice(source,witness,'clear')
    if proof in('unmeasured','measured'):
        assert choice is not None and choice['text']=='clear'
        assert choice['bbox']==full
        assert all(box[1]<=full[1]and box[3]>=full[3]for box in frames)
    else:assert choice is None,'Incomplete, unavailable or different contextual reads cannot certify the first word'


@pytest.mark.parametrize('evidence',['lone_third_guess','primary_counter','source_repeat','paired_third','paired_uncommon','explicit_doubt','failed_primary','strong_source'])
def test_a_new_isolated_word_guess_needs_corroboration_before_doubting_a_certain_model(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clcar sample stays readable.',confidence=30)
    if evidence=='strong_source':source[1]['confidence']=99
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    if evidence=='primary_counter':witness[1].update(text='clean',confidence=99)
    candidate='clcar'if evidence=='source_repeat'else'prlnted'if evidence=='paired_uncommon'else'clcat'
    def readings(word):
        left=[dict(w)for w in source];right=[dict(w)for w in source]
        left[1].update(text=candidate,confidence=99)
        right[1].update(text=candidate if evidence.startswith('paired_')else'clear',confidence=99 if evidence.startswith('paired_')else 30)
        return [left,right]
    witness.word_readings=readings
    if evidence=='failed_primary':witness.failure='Synthetic recognition failure';witness.model_reading=False
    result=pa.adjudicate('The clear sample stays readable.'.split(),source,witness,
        uncertain={1}if evidence=='explicit_doubt'else())
    note=result['word_notes'][1]
    if evidence=='lone_third_guess':
        assert result['tokens'][1]=='clear'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
    else:
        assert note['status']=='UNRESOLVED'
    assert result['marked']=={1},'Each changed source word keeps exactly its own reading record'


@pytest.mark.parametrize('ink',['clear','above','below','own_ascender','bounded_above','bounded_below'])
def test_original_word_scope_cannot_claim_clipped_ink_from_its_search_frame(ink):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=140,height=100)
        page.draw_rect(pymupdf.Rect(40,50,60,60),color=None,fill=(0,0,0))
        if ink in('above','bounded_above'):page.draw_rect(pymupdf.Rect(42,39,58,45),color=None,fill=(0,0,0))
        if ink in('below','bounded_below'):page.draw_rect(pymupdf.Rect(42,65,58,72),color=None,fill=(0,0,0))
        if ink=='own_ascender':page.draw_rect(pymupdf.Rect(45,40,55,52),color=None,fill=(0,0,0))
        limits=(47,None)if ink=='bounded_above'else(None,63)if ink=='bounded_below'else(None,None)
        scope=pa._word_ink_scope(SourceDisplay(drawing,0),dict(bbox=[40,50,60,60]),line_limits=limits)
    if ink in('above','below','own_ascender'):
        assert scope is None,'A cut component cannot prove the complete ink or ownership of this word'
    else:assert scope==pytest.approx([40,50,60,60],abs=.2)


@pytest.mark.parametrize('territory',['clear','upper_row','lower_row','detached_dot','touching_row','cut_own_ink'])
def test_word_scope_separates_neighboring_rows_before_expanding_its_letters(territory):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=160,height=100)
        for x in(60,65,70,75):
            page.draw_rect(pymupdf.Rect(x,50,x+3,54),color=None,fill=(0,0,0))
        if territory=='upper_row':page.draw_rect(pymupdf.Rect(60,44,78,47),color=None,fill=(0,0,0))
        if territory=='lower_row':page.draw_rect(pymupdf.Rect(60,57,78,60),color=None,fill=(0,0,0))
        if territory=='detached_dot':page.draw_rect(pymupdf.Rect(66,48,67,49),color=None,fill=(0,0,0))
        if territory=='touching_row':page.draw_rect(pymupdf.Rect(60,44,78,49),color=None,fill=(0,0,0))
        if territory=='cut_own_ink':page.draw_rect(pymupdf.Rect(60,44,63,51),color=None,fill=(0,0,0))
        scope=pa._word_ink_scope(SourceDisplay(drawing,0),dict(bbox=[60,50,78,54]))
    if territory in('touching_row','cut_own_ink'):
        assert scope is None,'Continuous or inseparable cut ink cannot prove a complete word'
    else:
        assert scope==pytest.approx([60,48 if territory=='detached_dot'else 50,78,54],abs=.2)


@pytest.mark.parametrize('proof',['complete','explicit_doubt','one_network_model','one_network_competitor','missing','different','still_clipped','competing'])
def test_matching_word_support_is_reread_when_late_ink_measurement_finds_a_cut(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The clear- sample stays readable.')
    for word in source[2:]:word['bbox']=[word['bbox'][0]+40,20,word['bbox'][2]+40,32]
    source[1].update(text='damaged-token',bbox=[20,20,60,32],confidence=99)
    whole=dict(text='clear-',bbox=[20,20,51,34],confidence=99)
    first=dict(whole,bbox=[20,20,50,34])
    witness=pa.CropWitness([dict(w)for w in source]);witness[1]=first;witness.model_reading=True
    witness.word_scope=lambda word:whole['bbox']
    def readings(word):
        if word['bbox'][3]<34:return[[dict(first)],[dict(first)]]
        if proof=='missing':return[]
        value=dict(whole)
        if proof=='different':value['text']='clean-'
        if proof=='still_clipped':value['bbox']=first['bbox']
        views=[[value],[dict(value)]]
        if proof=='one_network_model':views[1][0]['confidence']=20
        if proof=='one_network_competitor':views[1][0]['text']='clean-'
        if proof=='competing':views +=[[dict(whole,text='clean-')]]*2
        return views
    witness.word_readings=readings
    result=pa.adjudicate('The clear- sample stays readable.'.split(),source,witness,
        uncertain={1}if proof=='explicit_doubt'else())
    note=result['word_notes'][1]
    assert result['marked']=={1}
    if proof in('complete','explicit_doubt','one_network_model'):
        assert result['tokens'][1]=='clear-'and note['status']=='RESOLVED'
        assert note['method']=='complete_original_word_ink_corroboration'
        assert not note.get('source_image_only')
    else:assert note['status']=='UNRESOLVED'and note.get('source_image_only')


@pytest.mark.parametrize('proof',['dash','missing_letter','clipped_upper','non_dash_shape','weak_reading','different_word'])
def test_complete_original_terminal_dash_can_close_its_matching_word_evidence(tmp_path,monkeypatch,proof):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as drawing:
        page=drawing.new_page(width=180,height=120)
        page.insert_text((30,65),'clear!'if proof=='non_dash_shape'else'clear-',fontsize=20)
        native=page.get_text('words')[0]
        display=SourceDisplay(drawing,0)
        scope=pa._word_ink_scope(display,dict(bbox=list(native[:4])))
        assert scope is not None
        observed=list(scope);observed[2]-=.8
        if proof=='missing_letter':observed[0]+=(scope[2]-scope[0])*.45
        if proof=='clipped_upper':observed[1]+=3
        pixel=dict(text='clean-'if proof=='different_word'else'clear-',bbox=observed,
            confidence=60 if proof=='weak_reading'else 99)
        monkeypatch.setattr(pa,'_read_crop',lambda *args,**kwargs:[dict(pixel)])
        monkeypatch.setattr(pa,'_latin_directory',lambda engine:tmp_path)
        source=[dict(text='damaged-token',confidence=99,
            bbox=[scope[0],scope[1],scope[2]+1,scope[3]-2])]
        witness=pa.read_crop(display,[list(native[:4])],tmp_path);witness.model_reading=True
        witness.word_scope=lambda word:list(scope)
        result=pa.adjudicate(['clear-'],source,witness)
    note=result['word_notes'][0]
    assert result['marked']=={0}
    if proof=='dash':
        assert result['tokens']==['clear-']and note['status']=='RESOLVED'
        assert note['method']=='complete_original_terminal_dash_corroboration'
        assert not note.get('source_image_only')
    else:
        assert note['status']=='UNRESOLVED'
        assert note['method']!='complete_original_terminal_dash_corroboration'


@pytest.mark.parametrize('proof',['weak_tail','unavailable_tail_read','same_letters','source_counter','pixel_counter','uncertain_model','local_fallback','failed_pixels','different_prefix','shorter_tail','same_row','no_hyphen','punctuation_counter'])
def test_a_certain_complete_wrapped_word_needs_a_complete_counterreading(proof):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='Read',bbox=[110,20,135,32],confidence=99),
        dict(text='syn-'if proof!='no_hyphen'else'syn',bbox=[140,20,165,32],confidence=99),
        dict(text='uhetic',bbox=[20,38,58,50],confidence=30),
        dict(text='sample',bbox=[65,38,100,50],confidence=99)]
    if proof=='same_letters':source[2]['text']='thetic'
    if proof=='source_counter':source[2]['confidence']=99
    if proof=='punctuation_counter':source[2]['text']='uhetic;'
    if proof=='same_row':source[2]['bbox']=[172,20,210,32]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    if proof=='pixel_counter':witness[2].update(text='theory',confidence=99)
    if proof=='failed_pixels':witness.failure='local recognition failed'
    if proof=='unavailable_tail_read':witness.pop(2)
    model='Read '+('sympathetic'if proof=='different_prefix'else'synthe'if proof=='shorter_tail'else'synthetic')+' sample'
    result=pa.adjudicate(model.split(),source,witness,
        uncertain={1}if proof=='uncertain_model'else(),unresolved_source=proof=='local_fallback')
    assert result['marked'],'A changed OCR word always has its own source record'
    if proof in('weak_tail','unavailable_tail_read'):
        assert result['tokens']==['Read','synthetic','sample']
        assert result['marked']=={1}
        note=result['word_notes'][1]
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
        assert note['source']==['syn-','uhetic']and note['model']==['synthetic']
        assert note['source_range']==[1,3]and note['model_range']==[1,2]
        assert note.get('mapping')!='unplaced_within_span','The physical printed parts locate exactly one word'
    else:
        assert not any(d.get('method')=='complete_vision_reading_without_optical_counterevidence'
            and len(d.get('source',[]))>1 for d in result['decisions'])


@pytest.mark.parametrize('available',['base_only','adverb','neither','literal_candidate'])
def test_productive_inflection_does_not_certify_a_shared_fused_stroke(monkeypatch,available):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    lexicon={'read','the','sample','now'}
    if available=='base_only':lexicon.add('modern')
    if available=='adverb':lexicon.add('modernly')
    if available=='literal_candidate':lexicon.add('modemly')
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(lexicon))
    # Synthetic damaged spelling; the vocabulary supplies a veto, never output.
    source=words('Read the modemly sample now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda item:[[dict(item,text='modemly',confidence=99)]]*2
    result=pa.adjudicate('Read the modenly sample now'.split(),source,witness)
    note=result['word_notes'][2]
    if available in ('base_only','adverb'):
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert note['method']=='unproved_shared_stroke_identity'
        assert result['tokens'][2]!='modernly','A dictionary is not a transcription'
    else:
        assert note['status']=='RESOLVED'
        assert result['tokens'][2]=='modemly'


@pytest.mark.parametrize('case',['damaged_prefix','same_fragment','ordinary_spacing','justified_spacing','certain_model','visible_native','long_gap_without_neighbors'])
def test_explicitly_doubtful_fragment_needs_complete_word_evidence(case):
    from cps.services.reflow import pixel_adjudication as pa
    # Synthetic printed row: a blank damaged initial leaves only the tail ox.
    # Independent recognizers repeat that fragment; a complete-word claim needs
    # more than agreement on the remaining ink after the anomalous blank.
    source=words('Read the ox beside this clear sample now')
    for i,w in enumerate(source):w['bbox']=[i*22,10,i*22+18,20]
    source[2]['bbox'][0]+=5
    if case=='ordinary_spacing':source[2]['bbox'][0]-=5
    if case=='justified_spacing':
        for i,w in enumerate(source):w['bbox']=[i*28,10,i*28+18,20]
    if case=='long_gap_without_neighbors':source=source[2:3]
    target=0 if case=='long_gap_without_neighbors'else 2
    source[target]['text']='ox'if case=='same_fragment'else'0x'
    model=[w['text']for w in source];model[target]='ox'
    witness=pa.CropWitness([dict(w,text='ox'if i==target else w['text'])for i,w in enumerate(source)])
    witness.model_reading=True
    witness.word_readings=lambda item:[[dict(item,text='ox',confidence=99)]]*2
    result=pa.adjudicate(model,source,witness,uncertain=()if case=='certain_model'else(target,),
        source_print=case=='visible_native')
    note=result['word_notes'][target]
    if case in ('damaged_prefix','same_fragment'):
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert note['method']=='unproved_doubtful_leading_word_ink'
        assert target in result['marked']
    else:
        assert note['status']=='RESOLVED','Ordinary gaps or authoritative print do not invent new uncertainty'


@pytest.mark.parametrize('case',['matching','different_letters','missing_network','no_spaces','explicit_doubt','fallback','failed_pixels'])
def test_complete_ink_groups_prove_spaces_in_a_fused_four_word_reading(tmp_path,monkeypatch,case):
    import pymupdf
    import shutil
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    from cps.services.reflow.source_display import SourceDisplay
    text='Keep these clear samples'
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(text.casefold().split()))
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=100)
        page.insert_text((20,50),text.replace(' ','')if case=='no_spaces'else text,fontsize=12)
        printed=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        box=[min(w['bbox'][0]for w in printed),min(w['bbox'][1]for w in printed),
             max(w['bbox'][2]for w in printed),max(w['bbox'][3]for w in printed)]
        source=dict(text=text.replace(' ',''),bbox=box,confidence=99)
        def read(display,regions,cache,**kwargs):
            if case=='failed_pixels':raise RuntimeError('synthetic recognition failure')
            if case=='missing_network'and kwargs.get('language')=='LatinRaw':return []
            inside=[dict(w)for w in printed if any(r[0]-.5<=sum(w['bbox'][::2])/2<=r[2]+.5 for r in regions)]
            if len(inside)>1:return [dict(source)]
            if case=='different_letters'and inside:inside[0]['text']='other'
            return inside
        monkeypatch.setattr(pa,'_read_crop',read)
        monkeypatch.setattr(pa,'_latin_directory',lambda engine:tmp_path)
        locate=shutil.which
        monkeypatch.setattr(shutil,'which',lambda name:str(tmp_path/'synthetic-engine')if name=='tesseract'else locate(name))
        witness=pa.read_crop(SourceDisplay(doc,0),[box],tmp_path)
        witness.model_reading=True
        result=pa.adjudicate(text.split(),[source],witness,
            uncertain=(1,)if case=='explicit_doubt'else(),unresolved_source=case=='fallback')
    assert result['tokens']==([source['text']]if case=='fallback'else text.split())
    if case=='matching':
        assert not result['marked'],'Complete independently read groups prove the conserved spaces'
        assert not result['word_notes']
    else:
        assert result['marked'],'An unavailable, conflicting or rejected read supplies no boundary proof'


@pytest.mark.parametrize('case',['slash','dash','known_tail','complete_counter','accent','explicit_doubt','fallback','unknown_model','same_letters','native_print','unmeasured','different_case'])
def test_incomplete_ocr_prefix_is_not_a_complete_competing_model_word(monkeypatch,case):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    vocabulary={'read','the','garden','beside','this','sample','now','warden'}
    if case=='known_tail':vocabulary.add('arden')
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(vocabulary))
    original='-arden'if case=='dash'else '\\arden'
    if case=='accent':original='gárden'
    if case=='same_letters':original='-garden'
    if case=='different_case':original='\\Arden'
    source=words('Read the '+original+' beside this sample now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    if case!='unmeasured':witness.word_scope=lambda word:[word['bbox'][0]-1.5,*word['bbox'][1:]]
    if case=='complete_counter':witness[2].update(text='warden',bbox=[38.5,20,58,32])
    model='Read the '+('garthen'if case=='unknown_model'else'Garden'if case=='different_case'else'garden')+' beside this sample now'
    result=pa.adjudicate(model.split(),source,witness,
        uncertain=(2,)if case=='explicit_doubt'else(),
        unresolved_source=case=='fallback',source_print=case=='native_print')
    note=result['word_notes'][2]
    assert 2 in result['marked'],'Every changed reading retains its own word record'
    if case in('slash','dash'):
        assert result['tokens'][2]=='garden'
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert note['method']=='complete_vision_reading_without_optical_counterevidence'
    else:
        assert note['method']!='complete_vision_reading_without_optical_counterevidence'


@pytest.mark.parametrize('case',['two_weak_words','one_word','same_box','overlapping_row'])
def test_neighboring_row_positions_do_not_need_a_correct_lexical_reading(case):
    from cps.services.reflow import pixel_adjudication as pa
    # Independently located ink can bound a row even when its letters are weak.
    # This geometry supplies no spelling and cannot certify either word.
    neighbors=[dict(text='unreadable-a',bbox=[40,38,55,46],confidence=30),
        dict(text='unreadable-b',bbox=[59,38,75,46],confidence=20)]
    if case=='one_word':neighbors.pop()
    if case=='same_box':neighbors[1]['bbox']=neighbors[0]['bbox']
    if case=='overlapping_row':
        for word in neighbors:word['bbox'][3]=53
    top,bottom=pa._neighbor_line_limits([45,50,65,60],neighbors)
    assert bottom is None
    if case=='two_weak_words':assert top==48
    else:assert top is None,'An unproved or overlapping row cannot bound the original ink'


@pytest.mark.parametrize('case',['bounded','missing_prefix','cut_prefix','no_neighbors','different_row'])
def test_lazy_word_scope_stops_at_separately_located_neighbor_ink(tmp_path,monkeypatch,case):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    # Four synthetic letters with a tight adjacent word and a separate row.
    # Bounding the search may not erase an unobserved first part of this word.
    with pymupdf.open()as doc:
        page=doc.new_page(width=180,height=120)
        for x in(60,65,70,75):page.draw_rect(pymupdf.Rect(x,50,x+3,58),color=None,fill=(0,0,0))
        page.draw_rect(pymupdf.Rect(80,50,98,58),color=None,fill=(0,0,0))
        page.draw_rect(pymupdf.Rect(60,62,75,68),color=None,fill=(0,0,0))
        page.draw_rect(pymupdf.Rect(80,62,98,68),color=None,fill=(0,0,0))
        source=dict(text='sample',bbox=[60,50,78,58],confidence=99)
        if case=='missing_prefix':source['bbox'][0]=65
        if case=='cut_prefix':page.draw_rect(pymupdf.Rect(30,50,64,58),color=None,fill=(0,0,0))
        adjacent=dict(text='neighbor',bbox=[80,50,98,58],confidence=20)
        if case=='different_row':adjacent['bbox']=[80,80,98,88]
        rows=[source,adjacent]
        if case!='no_neighbors':rows.extend([dict(text='lower-a',bbox=[60,62,75,68],confidence=20),dict(text='lower-b',bbox=[80,62,98,68],confidence=20)])
        else:rows=[source]
        monkeypatch.setattr(pa,'_read_crop',lambda *a,**k:rows)
        witness=pa.read_crop(SourceDisplay(doc,0),[[60,50,98,58]],tmp_path)
        scope=witness.word_scope(source)
    if case in('bounded','missing_prefix'):
        assert scope==pytest.approx([60,50,78,58],abs=.2)
    else:assert scope is None,'Absent or contradictory ownership cannot certify a complete word'


@pytest.mark.parametrize('case',['nonword_tail','weak_prefix','complete_counter','strong_source','uncertain_model','fallback','no_division'])
def test_a_weak_divided_nonword_tail_cannot_overrule_a_complete_reader_word(monkeypatch,case):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset({'read','rainfall','rain','fall','fail','sample'}))
    # Synthetic print is rainfall, divided rain-/fall at the line boundary.
    source=[dict(text='Read',bbox=[100,20,130,32],confidence=99),
        dict(text='rain-'if case!='no_division'else'rain',bbox=[140,20,165,32],confidence=99),
        dict(text='fali',bbox=[20,38,40,50],confidence=99 if case=='strong_source'else 40),
        dict(text='sample',bbox=[50,38,90,50],confidence=99)]
    if case=='weak_prefix':source[1]['confidence']=40
    witness=pa.CropWitness([dict(w,confidence=99)for w in source]);witness.model_reading=True
    if case=='complete_counter':witness[2]['text']='fail'
    result=pa.adjudicate(['Read','rainfall','sample'],source,witness,
        uncertain={1}if case=='uncertain_model'else(),unresolved_source=case=='fallback')
    if case in('nonword_tail','weak_prefix'):
        assert result['tokens']==['Read','rainfall','sample']
        note=result['word_notes'][1]
        assert note['status']=='RESOLVED'and not note.get('source_image_only')
        assert result['marked']=={1},'A model correction keeps its own source control'
    else:assert not any(d.get('method')=='complete_vision_reading_without_optical_counterevidence'for d in result['decisions'])


@pytest.mark.parametrize('mode',['source_only','rejected_model','different_chosen'])
def test_restored_source_words_keep_their_own_occurrence_not_the_edit_envelope(mode):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Keep these clear printed samples here')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    model=[]if mode=='source_only'else['A','different','reading']
    if mode=='different_chosen':
        result=pa.adjudicate(model,source,pa.CropWitness([]))
        assert not all(result['boxes'].get(i)==w['bbox']for i,w in enumerate(source))
        return
    result=pa.adjudicate(model,source,witness,unresolved_source=True)
    assert result['tokens']==[w['text']for w in source]
    assert result['boxes']=={i:w['bbox']for i,w in enumerate(source)}
    for i,note in result['word_notes'].items():
        assert note['source']==note['chosen']==[source[i]['text']]
        assert not note.get('mapping'),'The source owner is known even when the model correspondence is not'
        assert not note.get('model'),'An unrelated model span cannot supply a positional word pairing'
        assert note['source_range']==[i,i+1]


@pytest.mark.parametrize('case',['doubtful','restored','certain_model','known_literal','remote','fragment','too_weak','native_print','missing_views'])
def test_an_explicitly_doubtful_nonword_cannot_ignore_its_own_optical_stroke_alternative(monkeypatch,case):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    vocab={'read','the','clear','sample','now'}
    if case=='known_literal':vocab.add('clcar')
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset(vocab))
    source=words('Read the clcar sample now')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    alternative=dict(source[2],text='clear',confidence=29 if case=='too_weak'else 35)
    if case=='remote':alternative['bbox']=[200,80,218,92]
    if case=='fragment':alternative['bbox']=[40,20,45,32]
    witness.word_readings=lambda word:[]if case=='missing_views'else[[alternative]]
    witness.word_context_readings=lambda word:[]
    model=['Read','a','different','line','now']if case=='restored'else[w['text']for w in source]
    result=pa.adjudicate(model,source,witness,uncertain=()if case in('certain_model','restored')else{2},
        unresolved_source=case=='restored',source_print=case=='native_print')
    at=result['tokens'].index('clcar')
    if case in('doubtful','restored'):
        note=result['word_notes'][at]
        assert note['status']=='UNRESOLVED'and note.get('source_image_only')
        assert note['source_bbox']==source[2]['bbox']
        assert note['alternative']==['clear']
        assert note['method']=='unsettled_optical_stroke_alternative'
        assert 'clear'not in result['tokens'],'A dictionary or weak alternate is never a transcription'
    else:
        assert not result.get('word_notes',{}).get(at,{}).get('source_image_only')
        if case=='certain_model':assert not result['marked'],'A missing proof cannot invent doubt in a certain complete reader word'


@pytest.mark.parametrize('case',['unrelated_lexical_guess','no_possible_stroke_word'])
def test_nonword_doubt_requires_an_actual_pixel_stroke_relation_and_avoids_irrelevant_reads(monkeypatch,case):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset({'clear','modernly','sample'}))
    token='modemly'if case=='unrelated_lexical_guess'else'examplezz'
    word=dict(text=token,bbox=[30,20,70,32],confidence=99)
    witness=pa.CropWitness([word]);calls=[]
    def read(item):calls.append(item);return[[dict(item,text='clear',confidence=99)]]
    witness.word_readings=read;witness.word_context_readings=read
    assert pa._unsettled_optical_nonword(word,witness)is None
    if case=='no_possible_stroke_word':assert not calls,'Vocabulary only filters eligibility; no possible stroke counter needs no OCR'


@pytest.mark.parametrize('case',['detached_prefix','complete','separate_neighbor','ambiguous_locations','fused_location','different_row'])
def test_complete_occurrence_location_seeds_ink_search_without_borrowing_other_words(tmp_path,monkeypatch,case):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    # Procedural strokes: a detached initial component, then four tail letters.
    # The second recognizer locates the whole occurrence without reading it.
    with pymupdf.open()as doc:
        page=doc.new_page(width=180,height=100)
        page.draw_rect(pymupdf.Rect(55,48,57,58),color=None,fill=(0,0,0))
        for x in(60,65,70,75):page.draw_rect(pymupdf.Rect(x,51,x+3,58),color=None,fill=(0,0,0))
        source=dict(text='rain',bbox=[60,51,78,58],confidence=99)
        if case=='complete':source['bbox']=[55,48,78,58]
        located=dict(text='lrain',bbox=[55,48,78,58],confidence=25)
        if case=='fused_location':located['bbox']=[55,48,115,58]
        if case=='different_row':located['bbox']=[55,80,78,90]
        locations=[located]
        if case=='ambiguous_locations':locations.append(dict(located,bbox=[54,48,78,58]))
        monkeypatch.setattr(pa,'_read_crop',lambda *args,**kwargs:locations)
        witness=pa.read_crop(SourceDisplay(doc,0),[[50,45,120,62]],tmp_path)
        if case=='separate_neighbor':
            witness.neighbor_occurrences=[source,dict(text='a',bbox=[55,48,57,58],confidence=20)]
        scope=witness.word_scope(source)
        assert scope is not None
        if case in('detached_prefix','complete'):
            assert scope==pytest.approx([55,48,78,58],abs=.2),'A separately located own initial stroke is part of the word, even without a correct spelling'
        else:
            assert scope==pytest.approx([60,51,78,58],abs=.2),'Ambiguous, oversized, different-row or separately owned ink is not an own-word certificate'
        if case=='detached_prefix':
            witness.word_readings=lambda word:[[dict(source,text='rain',confidence=99)]]*2
            witness.word_context_readings=lambda word:[]
            witness.alternates=lambda:[];witness.resampled=lambda:[];witness.model_reading=True
            result=pa.adjudicate(['grain'],[source],witness)
            assert result['tokens']==['grain'],'Repeated recognition of only the tail cannot erase the complete first reading'
            assert result['word_notes'][0]['method']=='complete_vision_reading_without_optical_counterevidence'


@pytest.mark.parametrize('case',['missing_initial','longer_tail','weak_tail','ordinary_spaces','uniform_justification','same_word','visible_native','isolated_word','other_row','missing_suffix'])
def test_repeated_tail_cannot_certify_losing_a_prefix_after_an_anomalous_blank(case):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('Read the at beside this clear sample now')
    for i,w in enumerate(source):w['bbox']=[i*22,10,i*22+18,20]
    source[2]['bbox'][0]+=5
    model_word='cat';tail='at'
    if case=='longer_tail':model_word='grain';tail='rain'
    if case=='weak_tail':source[2]['confidence']=20
    if case=='ordinary_spaces':source[2]['bbox'][0]-=5
    if case=='uniform_justification':
        for i,w in enumerate(source):w['bbox']=[i*28,10,i*28+18,20]
    if case=='other_row':source[2]['bbox'][1:4:2]=[30,40]
    if case=='missing_suffix':model_word='cats';tail='cat'
    source[2]['text']=tail
    if case=='isolated_word':source=source[2:3]
    at=0 if case=='isolated_word'else 2
    model=[w['text']for w in source];model[at]=tail if case=='same_word'else model_word
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda item:[[dict(item,text=tail,confidence=99)]]*2
    witness.word_scope=lambda item:list(item['bbox'])
    result=pa.adjudicate(model,source,witness,source_print=case=='visible_native')
    if case in('missing_initial','longer_tail','weak_tail'):
        note=result['word_notes'][at]
        assert note['status']=='UNRESOLVED'and note.get('source_image_only'),'Agreement on surviving ink cannot certify the missing initial letters'
        assert at in result['marked']
        assert note['method']=='unproved_doubtful_leading_word_ink'
    elif case=='same_word':
        assert result['tokens'][at]==tail and at not in result['marked'],'No changed first reading means no new claim that an initial is missing'
    else:
        assert result['tokens'][at]==tail
        assert result['word_notes'][at]['status']=='RESOLVED','Normal spacing, explicit print or insufficient geometry must not invent missing ink'


@pytest.mark.parametrize('obstruction',['none','different_prefix','no_division','other_owner','distant_tail','same_row','intervening_row','ambiguous_anchor'])
def test_clipped_divided_word_recovers_its_unclaimed_prefix_before_tail_adjudication(obstruction):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    optical=[]
    for row,text in enumerate(['Read this clear before rain-','fall covers the open garden today.']):
        for col,token in enumerate(text.split()):
            optical.append(dict(text=token,bbox=[10+col*30,20+row*15,33+col*30,30+row*15],confidence=99))
    prefix=optical[4];tail=optical[5]
    if obstruction=='different_prefix':prefix['text']='hail-'
    if obstruction=='no_division':prefix['text']='rain'
    if obstruction=='distant_tail':
        for w in optical[5:]:w['bbox'][1]+=60;w['bbox'][3]+=60
    if obstruction=='same_row':tail['bbox']=[160,20,183,30]
    if obstruction=='intervening_row':optical.append(dict(text='Other',bbox=[10,31,33,34],confidence=99))
    text='Read this clear before rainfall covers the open garden today.'
    if obstruction=='ambiguous_anchor':text+=' this clear before'
    block=dict(id='sample',role='body',text=text,bbox=[0,0,1000,1000])
    regions={'sample':[[5,18,126,31],[5,34,190,46]],'_instances':{vp._source_instance(w):'sample'for w in optical if w is not prefix}}
    if obstruction=='distant_tail':regions['sample'][1]=[5,94,190,106]
    if obstruction=='same_row':regions['sample'][1]=[5,18,190,46]
    if obstruction=='other_owner':regions['_instances'][vp._source_instance(prefix)]='foreign'
    result=vp._complete_optical_row_territory(block,dict(blocks=[block]),optical,
        SimpleNamespace(rect=pymupdf.Rect(0,0,260,160)),regions,{})
    assert result==([prefix]if obstruction=='none'else[]),'A uniquely anchored printed division can recover only its own exact unclaimed prefix'


@pytest.mark.parametrize('case',['overlapping_ascenders','separate_rows','same_row','near_same_row','deep_overlap','distant_row','no_reset','different_letters'])
def test_exact_printed_division_uses_row_centers_not_disjoint_letter_boxes(case):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='rain-',bbox=[140,20,170,32],confidence=95),
            dict(text='fall',bbox=[20,29,42,41],confidence=95)]
    if case=='separate_rows':source[1]['bbox']=[20,34,42,46]
    if case=='same_row':source[1]['bbox']=[180,20,202,32]
    if case=='near_same_row':source[1]['bbox']=[20,23,42,35]
    if case=='deep_overlap':source[1]['bbox']=[20,25,42,37]
    if case=='distant_row':source[1]['bbox']=[20,80,42,92]
    if case=='no_reset':source[1]['bbox']=[180,29,202,41]
    model='rainfalls'if case=='different_letters'else'rainfall'
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    result=pa.adjudicate([model],source,witness)
    if case in('overlapping_ascenders','separate_rows'):
        assert result['tokens']==['rainfall']and not result['marked'],'Conserved printed letters on adjacent physical rows are a joined word, even when glyph boxes overlap vertically'
        assert result['conserved_boundaries']=={}
    else:
        assert pa._proved_wrap(source,model,witness,same_reading=True)is None,'Same-row, overlapping-line, distant, unreset or changed-letter input cannot prove a printed division'


@pytest.mark.parametrize('evidence',['complete_context','different_letters','clipped_prefix','unavailable_context','conflicting_context'])
def test_exact_divided_letters_can_be_corroborated_in_complete_context(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='rain-',bbox=[140,20,170,30],confidence=60),
            dict(text='fall',bbox=[20,35,42,45],confidence=99)]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[]
    def context(word):
        first=dict(word,confidence=99)
        if evidence=='different_letters':first['text']='hail-'
        if evidence=='clipped_prefix':first['bbox']=[150,20,170,30]
        if evidence=='unavailable_context':return []
        views=[[first],[dict(first)]]
        if evidence=='conflicting_context':views.extend([[dict(word,text='hail-',confidence=99)]]*2)
        return views
    witness.word_context_readings=context
    result=pa.adjudicate(['rainfall'],source,witness)
    if evidence=='complete_context':
        assert result['tokens']==['rainfall']and not result['marked'],'A complete independent contextual pair can prove the already conserved prefix; isolated-word failure is not doubt in the joined word'
    else:
        assert pa._proved_wrap(source,'rainfall',witness,same_reading=True)is None,'A clipped, absent or competing contextual reading cannot certify a source division'


@pytest.mark.parametrize('context',['complete','clipped','different_word','conflicting_pairs','explicit_doubt'])
def test_reference_lexical_view_keeps_contextual_word_adjudication(context):
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    tokens='A modern <n:note> sample remains readable.'.split()
    source=words('A modem 2 sample remains readable.')
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(w)for w in source]]*2
    calls=[]
    def contexts(word):
        calls.append(word)
        view=words('A '+('modem'if context=='different_word'else'modern')+' 2 sample remains readable.')
        if context=='clipped':view[1]['bbox']=[27,20,38,32]
        return [view,view]+([words('A modem 2 sample remains readable.')]*2 if context=='conflicting_pairs'else[])
    witness.word_context_readings=contexts
    proof={2:dict(status='PROVED',label='2',source_bbox=list(source[2]['bbox']))}
    model,projected=vp._lexical_marker_view(tokens,witness,proof)
    _,baseline=vp._lexical_marker_view(tokens,source,proof)
    assert not calls,'Reserving reference ink is lazy'
    result=pa.adjudicate(model,baseline,projected,uncertain={1}if context=='explicit_doubt'else())
    note=result['word_notes'][1]
    assert result['tokens'].count('<n:note>')==1
    if context=='complete':
        assert result['tokens'][1]=='modern'and note['status']=='RESOLVED'
        assert not note.get('source_image_only')and note['method']=='contextual_pixel_corroboration_of_model','Reference reservation cannot discard the independent proof available to an ordinary prose word'
        assert calls
        assert all(w['text']!='2'for view in projected.word_context_readings(source[1])for w in view),'The same raised ink stays reserved in the contextual read'
    else:
        assert note['status']=='UNRESOLVED'and note.get('source_image_only'),'Incomplete, conflicting or explicitly doubtful evidence stays unresolved after projection'


@pytest.mark.parametrize('capability',['word_gap','dash_shape','fragment_boundary','word_end_dash'])
def test_reference_lexical_view_preserves_original_pixel_proofs(capability):
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    witness=pa.CropWitness(words('A clear sample.'));calls=[]
    def proof(*args):calls.append(args);return True
    setattr(witness,capability,proof)
    _,projected=vp._lexical_marker_view([],witness,{})
    assert not calls
    assert getattr(projected,capability,None)is proof,'Reserving a reference changes lexical inventory, not the available original-ink measurements'
    assert getattr(projected,capability)(witness[0])and len(calls)==1


@pytest.mark.parametrize('case',['eroded_numeral','same_small_type','raised_small_type','other_row','letters','complete_ink','unmeasured_ink','native_paint','equal_length','complete_small_numeral'])
def test_an_eroded_numeric_fragment_cannot_certify_unseen_digits(case):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='oo',bbox=[20,26,32,29],confidence=35),
            dict(text='out',bbox=[38,22,53,29],confidence=95),
            dict(text='of',bbox=[59,22,68,29],confidence=95),
            dict(text='300',bbox=[74,20,92,29],confidence=95)]
    model=['700','out','of','300']
    if case=='same_small_type':source[-1]['bbox']=[74,26,80,29]
    if case=='raised_small_type':source[0]['bbox']=[20,20,32,23]
    if case=='other_row':source[-1]['bbox']=[74,40,92,49]
    if case=='letters':model[0]='zoo'
    if case=='complete_ink':source[0]['bbox']=[20,20,38,29]
    if case=='equal_length':model[0]='70'
    if case=='complete_small_numeral':source[0]['bbox']=[20,26,26,29]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    witness.word_readings=lambda word:[]
    witness.word_context_readings=lambda word:[]
    witness.word_scope=lambda word:None if case=='unmeasured_ink'else list(word['bbox'])
    result=pa.adjudicate(model,source,witness,source_print=case=='native_paint')
    note=result['word_notes'].get(0,{})
    if case=='eroded_numeral':
        assert note.get('source_image_only')and note['status']=='UNRESOLVED','A digit-sized original fragment at only a third of its same-row digit height cannot certify the missing numeric ink'
        assert note['method']=='incomplete_numeric_word_ink'
        assert note['source_bbox']==[20,20,32,29]
    else:
        assert not note.get('source_image_only'),'Small or raised type, another row, complete original ink, letters and missing measurements do not prove eroded numeric print'


@pytest.mark.parametrize('evidence',['single_weak','single_strong','paired','clipped_pair','different_pair','missing_scope','conflicting_pair','already_spaced_model','no_gap'])
def test_agreed_source_word_is_not_split_by_one_layout_segmentation(evidence):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text='Read',bbox=[10,20,32,32],confidence=99),
            dict(text='Panel11',bbox=[40,20,100,32],confidence=99),
            dict(text='now.',bbox=[110,20,130,32],confidence=99)]
    confidence=63 if evidence=='single_weak'else 99
    parts=[dict(text='Panel1',bbox=[40,22,80,30],confidence=confidence),
           dict(text='1',bbox=[82,24,100,30],confidence=confidence)]
    witness=pa.CropWitness([source[0],*parts,source[-1]]);witness.model_reading=True
    witness.word_gap=lambda left,right:evidence!='no_gap'
    witness.word_scope=lambda word:None if evidence=='missing_scope'else[40,22,100,30]
    def reads(word):
        if evidence in('single_weak','single_strong','already_spaced_model','no_gap'):return []
        left=[dict(w,confidence=99)for w in witness];right=[dict(w)for w in left]
        if evidence=='clipped_pair':left[1]['bbox']=[46,22,80,30];right[1]['bbox']=[46,22,80,30]
        if evidence=='different_pair':right[2]['text']='I'
        views=[left,right]
        if evidence=='conflicting_pair':views.extend([[source[0],source[1],source[-1]]]*2)
        return views
    witness.word_readings=reads
    model='Read Panel1 1 now.'.split()if evidence=='already_spaced_model'else'Read Panel11 now.'.split()
    result=pa.adjudicate(model,source,witness,unresolved_source=True)
    if evidence in('paired','already_spaced_model'):
        assert result['tokens']==['Read','Panel1','1','now.'],'Complete independent boundaries or the existing spaced first reading still conserve genuine printed words'
    else:
        assert result['tokens']==['Read','Panel11','now.'],'A weak, isolated, clipped or conflicting segmentation must not invent a space inside a source/model-agreed word'
        assert not any(d.get('source_image_only')for d in result['word_notes'].values()),'A speculative boundary must not withhold an otherwise retained source word as two ink fragments'


@pytest.mark.parametrize('evidence',['small_edges','outer_weak','weak_outer_only','boundary_weak','large_edges','missing_scope','different_anchor','ink_present'])
def test_empty_printed_gap_uses_its_own_complete_boundary_words(evidence):
    """Procedural ink edges and fabricated recognition labels isolate gap proof."""
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    edge=0 if evidence in('weak_outer_only','boundary_weak')else .8 if evidence=='large_edges'else .2
    source=[dict(text='Keep',bbox=[2,20,18,30],confidence=99),
        dict(text='the',bbox=[20,20,40,30],confidence=99),
        dict(text='panel.',bbox=[50,20,70,30],confidence=99),
        dict(text='Next',bbox=[2,40,18,50],confidence=0 if evidence in('outer_weak','weak_outer_only')else 99),
        dict(text='line.',bbox=[22,40,40,50],confidence=99)]
    if evidence=='boundary_weak':source[2]['confidence']=20
    witness=pa.CropWitness([dict(w,bbox=list(w['bbox']))for w in source]);witness.model_reading=True
    witness[1]['bbox'][2]+=edge;witness[2]['bbox'][0]-=edge
    if evidence=='different_anchor':witness[2]['text']='panei.'
    def scope(word):
        if evidence=='missing_scope':return None
        return list(witness[1]['bbox']if word['text']=='the'else witness[2]['bbox']if word['text']=='panel.'else word['bbox'])
    witness.word_scope=scope
    with pymupdf.open()as doc:
        page=doc.new_page(width=100,height=70)
        for box in(witness[1]['bbox'],witness[2]['bbox']):page.draw_rect(pymupdf.Rect(box),color=None,fill=(0,0,0))
        if evidence=='ink_present':page.draw_rect(pymupdf.Rect(44,22,47,29),color=None,fill=(0,0,0))
        display=SourceDisplay(doc,0)
        result=pa.adjudicate('Keep the extra panel. Next line.'.split(),source,witness,
            absence_proof=lambda left,right:pa.blank_between(display,left,right,[[0,0,100,70]]))
    if evidence in('small_edges','outer_weak','weak_outer_only'):
        assert result['tokens']=='Keep the panel. Next line.'.split(),'Positive empty original ink between complete adjacent words must reject the invented word even when a farther row is unreadable'
        decision=next(d for d in result['decisions']if d['model']==['extra'])
        assert decision['chosen']==[] and decision['status']=='RESOLVED'
    else:
        assert 'extra'in result['tokens'],'Weak or different boundary words, missing or excessive edge measurement, and actual intervening ink cannot prove a word absent'


@pytest.mark.parametrize('intervening_ink',[False,True])
def test_empty_gap_raster_does_not_round_a_neighbor_stroke_into_the_gap(intervening_ink):
    """Procedurally drawn raster words leave a fractional empty original band."""
    import io
    import pymupdf
    from PIL import Image,ImageDraw
    from cps.services.reflow import pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    factor=8
    image=Image.new('RGB',(100*factor,60*factor),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((20*factor,20*factor,int(40.2*factor),30*factor),fill='black')
    draw.rectangle((round(49.12*factor),20*factor,70*factor,30*factor),fill='black')
    if intervening_ink:draw.rectangle((44*factor,22*factor,round(44.3*factor),29*factor),fill='black')
    encoded=io.BytesIO();image.save(encoded,format='PNG')
    left=dict(bbox=[20,20,40.2,30]);right=dict(bbox=[49.12,20,70,30])
    with pymupdf.open()as doc:
        page=doc.new_page(width=100,height=60)
        page.insert_image(page.rect,stream=encoded.getvalue())
        assert pa.blank_between(SourceDisplay(doc,0),left,right,[[0,0,100,60]])is not intervening_ink,'A fractional neighboring edge is outside the empty original band; genuine thin intervening ink still defeats absence proof'


@pytest.mark.parametrize('case',['weak_prefix','weak_tail','complete_counter','printed_quote','explicit_doubt','local_only','punctuation_added','unknown_model','different_case','real_tail','strong_source','damaged_initial'])
def test_weak_nonlexical_punctuation_tail_cannot_dispute_a_complete_reader_word(case):
    from cps.services.reflow import pixel_adjudication as pa
    source=words('The \u2018rden sample remains clear.')
    model='The garden sample remains clear.'.split()
    if case=='weak_tail':source[1]['text']='rden/'
    if case=='printed_quote':source[1]['text']='\u2018garden'
    if case=='punctuation_added':model[1]='garden!'
    if case=='unknown_model':model[1]='zrden'
    if case=='different_case':source[1]['text']='\u2018Rden'
    if case=='real_tail':source[1]['text']='\u2018rain';model[1]='grain'
    for word in source:word['confidence']=99
    source[1]['confidence']=99 if case=='strong_source'else 20
    if case=='damaged_initial':
        for word in source[1:]:word['bbox']=[v+10 if j in(0,2)else v for j,v in enumerate(word['bbox'])]
    witness=pa.CropWitness([dict(w)for w in source]);witness.model_reading=True
    if case=='complete_counter':witness[1]=dict(source[1],text='warden',confidence=99)
    witness.word_readings=lambda word:[[dict(w)for w in witness],[dict(w)for w in witness]]
    if case=='damaged_initial':witness.word_scope=lambda word:list(word['bbox'])
    result=pa.adjudicate(model,source,witness,uncertain={1}if case=='explicit_doubt'else(),unresolved_source=case=='local_only')
    note=result['word_notes'].get(1,{})
    if case in('weak_prefix','weak_tail'):
        assert result['tokens'][1]=='garden'and note['status']=='RESOLVED','Punctuation standing in for missing initial letters at a weak nonword is not a complete optical counter to a certain first reader'
        assert not note.get('source_image_only')
    elif case=='complete_counter':
        assert result['tokens'][1]=='warden'and note['status']=='RESOLVED','A complete independently corroborated alternative still has authority to correct the first reading'
    else:
        assert note.get('status')!='RESOLVED','Explicit doubt, real quote, added punctuation, unknown spelling or mismatched tail case still needs positive pixel proof'


@pytest.mark.parametrize('case',['short_noise','different_noise','long_noise','complete_counter','strong_source','explicit_doubt','fallback','unknown_model','different_prefix','same_row','clipped_abbreviation','complete_abbreviation','neighbor_ink'])
def test_divided_word_counterevidence_is_judged_as_a_whole_lexical_word(monkeypatch,case):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset({'read','rainfall','rainbow','rain','fall','bow','sample','fxd'}))
    # Synthetic print divides rainfall as rain-/fall. Fragment OCR supplies
    # different incomplete tails; none is a proved replacement whole word.
    tail={'short_noise':'fll','different_noise':'ucd','long_noise':'fxlll','complete_counter':'bow'}.get(case,'ucd')
    source=[dict(text='Read',bbox=[100,20,130,32],confidence=99),
        dict(text='rain-',bbox=[140,20,165,32],confidence=99),
        dict(text=tail,bbox=[20,38,40,50],confidence=99 if case=='strong_source'else 80),
        dict(text='sample',bbox=[50,38,90,50],confidence=99)]
    if case in('clipped_abbreviation','complete_abbreviation','neighbor_ink'):
        source[2]['text']='fxd'
    if case=='clipped_abbreviation':source[2]['bbox'][0]=22
    if case=='different_prefix':source[1]['text']='plain-'
    if case=='same_row':source[2]['bbox']=[170,20,190,32]
    witness=pa.CropWitness([dict(item,confidence=99)for item in source]);witness.model_reading=True
    if case=='clipped_abbreviation':witness.word_scope=lambda word:[20,38,40,50]if word['text']=='fxd'else list(word['bbox'])
    if case=='neighbor_ink':
        witness.word_scope=lambda word:[15,38,40,50]if word['text']=='fxd'else list(word['bbox'])
        witness.append(dict(text='a',bbox=[15,38,19,50],confidence=99))
    witness.word_readings=lambda word:[[dict(item)for item in witness]]*2
    model='rainxyz'if case=='unknown_model'else'rainfall'
    result=pa.adjudicate(['Read',model,'sample'],source,witness,
        uncertain={1}if case=='explicit_doubt'else(),unresolved_source=case=='fallback')
    retained=any(d.get('method')=='complete_vision_reading_without_optical_counterevidence'for d in result['decisions'])
    if case in('short_noise','different_noise','clipped_abbreviation'):
        assert result['tokens']==['Read','rainfall','sample']
        assert retained and result['word_notes'][1]['status']=='RESOLVED'
        assert result['marked']=={1},'The actual model correction remains linked to its own original ink'
    else:assert not retained,'A genuine counterword, strong original, deliberate doubt or unproved division stays independently adjudicated'


@pytest.mark.parametrize('case',['body','footnote','foreign_owner','foreign_region','ambiguous_anchor','different_letters','distant_prefix','different_row','missing_prefix'])
def test_a_first_line_indent_cannot_cut_an_internal_printed_row_opening(case):
    from types import SimpleNamespace
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    first='Clear source anchors keep this line.'
    second='Quiet visitors bring small bags home now.'
    tokens=first.split()+second.split()
    pixels=[dict(text=t,bbox=[100+30*i,20,125+30*i,32],confidence=99)for i,t in enumerate(first.split())]
    pixels +=[dict(text=t,bbox=[30,50,55,62]if i==0 else[60,50,90,62]if i==1 else[100+30*(i-2),50,125+30*(i-2),62],confidence=99)for i,t in enumerate(second.split())]
    block=dict(id='p1',role='footnote'if case=='footnote'else'body',text=' '.join(tokens),bbox=[237,100,850,450],marker='1')
    answer={'blocks':[block]};regions={'p1':[[95,18,310,65]]}
    if case=='foreign_owner':regions['_instances']={vp._source_instance(pixels[6]):'p2'}
    if case=='foreign_region':
        other=dict(id='p2',role='body',text='Quiet',bbox=[60,300,150,400]);answer['blocks'].append(other);regions['p2']=[[25,48,58,65]]
    if case=='ambiguous_anchor':block['text']+=' bring small bags'
    if case=='different_letters':pixels[6]['text']='Qulet'
    if case=='distant_prefix':pixels[6]['bbox']=[0,50,10,62]
    if case=='different_row':pixels[6]['bbox'][1:4:2]=[80,92]
    if case=='missing_prefix':pixels.remove(pixels[6])
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,400,160))
    added=vp._complete_optical_row_territory(block,answer,pixels,display,regions,{})
    if case in('body','footnote'):
        assert [v['text']for v in added]==['Quiet','visitors']
        widened=dict(regions);widened['p1']=regions['p1']+[list(v['bbox'])for v in added]
        widened['_row_additions']={'p1':tuple(vp._source_instance(v)for v in added)}
        widened['_instances']={vp._source_instance(v):'p1'for v in added}
        local=vp._owned_block_words(block,answer,pixels,display,widened,{},ordered=True)
        assert [v['text']for v in local]==tokens,'Added opening words stay on their own row, before its existing words'
    else:assert not added,'A clipped rectangle cannot take another owner, guess letters or jump a gap'


@pytest.mark.parametrize('optical_document',[False,True])
def test_optically_authored_native_map_cannot_certify_a_confusable_nonword(tmp_path,monkeypatch,optical_document):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow import text_quality
    printed='The surnmer garden stays green.'
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:{'the','summer','garden','stays','green'})
    with pymupdf.open()as doc:
        leaf=doc.new_page(width=360,height=150);leaf.insert_text((25,60),printed,fontsize=12)
        if optical_document:doc.set_metadata({'producer':'Paper Capture Plug-in with ClearScan'})
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in leaf.get_text('words')]
        # Identical recognition of an OCR-authored map is not independent
        # evidence that its suspicious letter decoding is correct.
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        page=dict(pno=0,body=['<p>'+printed+'</p>'],asides=[])
        records=[dict(id='native-0',role='body',bbox=[20,40,340,75],source_regions=[[20,40,340,75]])]
        class Package:
            def image(self,*a,**k):pass
        vp._audit_native_pixels(doc,page,records,tmp_path,Package(),{})
    if optical_document:
        assert records[0]['adjudication'][0]['method']=='optical_map_confusable_nonword'
        assert records[0]['flagged_readings']['1']['tier']=='flagged'
        assert 'reflow-flagged-reading'in page['body'][0]
        assert 'source-word-image'in page['body'][0]or'reflow-unit-print'in page['body'][0]
        assert records[0]['adjudication'][0]['status']=='UNRESOLVED'
    else:
        assert records[0]['pixel_adjudication_status']=='VISIBLE_NATIVE_PRINT'
        assert page['body']==['<p>'+printed+'</p>']


@pytest.mark.parametrize('source_prefix',['some-','other-'])
def test_page_compilation_keeps_a_divided_word_complete_when_page_ocr_misreads_its_prefix(tmp_path,monkeypatch,source_prefix):
    """Synthetic ink divides something; OCR's zero supplies geometry only."""
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    printed=['Read this clear before some-','thing covers the open garden today.']
    with pymupdf.open()as original,pymupdf.open()as scan:
        page=original.new_page(width=400,height=180)
        for y,line in zip([60,80],printed):page.insert_text((20,y),line,fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        optical=[dict(w,text='s0me-'if w['text']=='some-'else w['text'])for w in source]
        prefix=source[4];source[4]=dict(prefix,text=source_prefix)
        copied=scan.new_page(width=400,height=180);copied.insert_image(copied.rect,pixmap=page.get_pixmap())
        witness=pa.CropWitness([dict(w,text='some-'if w['text']==source_prefix else w['text'])for w in source])
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:witness)
        model='Read this clear before something covers the open garden today.'
        b=block(model);regions={'b1':[[18,44,prefix['bbox'][0]-2,65],[18,65,360,85]],
            '_instances':{vp._source_instance(w):'b1'for w in optical if w['text']!='s0me-'}}
        class Package:
            def image(self,*a):pass
        compiled,receipt=vp.compile_page(dict(blocks=[b]),source,SourceDisplay(scan,0),0,
            Package(),{},pixel_words=optical,regions=regions,body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    record=receipt['blocks'][0]
    if source_prefix=='some-':
        assert any(g['kind']=='complete_optical_row_territory'for g in record['guard'])
        assert record['published_tokens']==model.split()
        assert 'thing'not in record['published_tokens'],'A crop of a tail cannot certify the complete lexical word'
    else:
        assert not any(g['kind']=='complete_optical_row_territory'for g in record['guard'])
