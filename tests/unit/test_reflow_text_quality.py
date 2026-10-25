"""Synthetic text controls for per-page lexical and source-script admission."""
import copy
from pathlib import Path
import zipfile

import pymupdf
import pytest

from cps.services.reflow import assess, text_quality, vision_pipeline
from tests.unit.test_reflow_vision_pilot import SyntheticReader, answer, block

pytestmark = pytest.mark.unit
GOOD = 'Green baskets contain ripe apples while purple carts carry fresh bread.'
BAD = 'PaTchWOrk roOtIng blorple fliMmEd Zzxqv thAte'
LEXICON = frozenset(GOOD.lower().rstrip('.').split()) | {'patchwork', 'rooting'}


def test_internal_case_with_dictionary_misses_rejects_even_short_headings():
    result = text_quality.inspect(BAD, lexicon=LEXICON)
    assert result['needs_read']
    assert result['case_flip_words'] == 4
    assert result['dictionary_misses'] == 4
    assert 'internal_case_and_dictionary_misses' in result['reasons']
    assert not text_quality.inspect(GOOD.upper(), lexicon=LEXICON)['needs_read']
    assert not text_quality.inspect(GOOD.title(), lexicon=LEXICON)['needs_read']
    assert text_quality.inspect((GOOD+' ')*20+'\n'+BAD,lexicon=LEXICON)['needs_read']
    assert text_quality.inspect('coNtAiNeR',lexicon={'container'})['needs_read']
    assert not text_quality.inspect('BrightForms maker joins CedarWorks today',lexicon={'maker','joins','today'})['needs_read']


def test_dense_unrecognizable_lowercase_passage_cannot_hide_in_clean_page():
    damage = ' '.join('zqxv' + chr(97+n) for n in range(16))
    result = text_quality.inspect((GOOD+' ')*20 + damage, lexicon=LEXICON)
    assert result['dictionary_miss_rate'] < .10
    assert result['worst_window_miss_rate'] == 1
    assert result['needs_read']
    assert result['case_flip_words'] == 0


def test_non_latin_words_are_not_english_misses_and_lost_script_is_rejected():
    # Letter combinations are written for this test, not a quoted passage.
    original = 'αλφαβήτα γαμμαδέλτα καππαλάμδα'
    metric = text_quality.inspect(original, lexicon=LEXICON)
    assert metric['latin_words'] == 0 and not metric['needs_read']
    assert text_quality.script_mismatches(original, GOOD) == ['GREEK']
    assert text_quality.script_mismatches(original, original) == []
    assert text_quality.script_mismatches('α + β = γ', GOOD) == []


def test_every_page_and_its_actual_notes_are_swept_without_generated_disclosures():
    pages = [dict(pno=0,body=['<p>'+GOOD+'</p>'],asides=[]),
             dict(pno=1,body=['<h2>'+GOOD+'</h2>'],asides=['<aside>'+BAD+'</aside>'])]
    records = {0:dict(source='native'),1:dict(source='native')}
    sweep = text_quality.sweep_pages(pages, records, [0,1], lexicon=LEXICON)
    assert sweep['checked_pages'] == [0,1]
    assert sweep['pages'][0]['passed'] and not sweep['pages'][1]['passed']
    with pytest.raises(ValueError,match='Every source page'):
        text_quality.validate_sweep(sweep,[0,1])
    pages[1]['asides']=['<aside class="vision-disclosure">'+BAD+'</aside>']
    clean = text_quality.sweep_pages(pages,records,[0,1],lexicon=LEXICON)
    text_quality.validate_sweep(clean,[0,1])
    with pytest.raises(ValueError,match='exactly once'):
        text_quality.sweep_pages(pages[:1],records,[0,1],lexicon=LEXICON)
    incomplete=copy.deepcopy(clean);incomplete['pages'].pop(1)
    with pytest.raises(ValueError,match='Every source page'):
        text_quality.validate_sweep(incomplete,[0,1])


def test_missing_dictionary_cannot_certify_a_text_layer(monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:None)
    metric=text_quality.inspect(GOOD)
    assert metric['needs_read'] and not metric['dictionary_available']
    assert metric['reasons']==['dictionary_unavailable']


def test_joined_word_is_rechecked_and_recovers_both_source_pages(tmp_path,monkeypatch):
    from cps.services.reflow import build_epub,unit_recovery
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset({'container'}))
    pages=[dict(pno=0,body=['<p id="left">coNt-</p>'],asides=[],anchor='<span id="pg_0000"></span>'),
           dict(pno=1,body=['<p>AiNeR</p>'],asides=[],anchor='<span id="pg_0001"></span>')]
    records={n:dict(source='vision',blocks=[dict(id='b1',role='body')]) for n in (0,1)}
    initial=text_quality.sweep_pages(pages,records,[0,1])
    assert initial['passed']
    controller=unit_recovery.Controller(SyntheticReader(),[0,1],tmp_path/'recovery')
    controller.records=records
    for page in pages:controller.register_page(page,records[page['pno']])
    with unit_recovery.activated(controller):
        assert build_epub._join_page_turns(pages,layout_pages={0,1},
            layout_boundaries={1:dict(role='paragraph',hyphen='drop')}) == 1
        assert 'coNtAiNeR' in text_quality.reading_text(pages[0])
        with pytest.raises(unit_recovery.UnitFailure,match='Joined reading') as failure:
            vision_pipeline._compiled_text_quality(pages,records,[0,1])
    assert failure.value.pages == [0,1]
    assert failure.value.stage == 'compiled_publication_text_quality'
    assert not records[0]['compiled_text_quality']['passed']


def test_bad_native_text_is_not_classified_as_clean(monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_text((30,60),BAD)
        assert not vision_pipeline.clean_native(page)
        raw = (GOOD+' ')*4 + BAD
        assert not assess.looks_like_prose((BAD+' ')*10)
        assert text_quality.inspect(raw)['case_flip_words'] == 4


def test_final_package_rejects_damage_exposed_by_an_admitted_page_seam(tmp_path,monkeypatch):
    from cps.services.reflow import vision_pixels
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:frozenset({'container'}))
    monkeypatch.setattr(vision_pipeline,'clean_native',lambda *a,**k:False)
    monkeypatch.setattr(vision_pixels,'words',lambda display,*a,**k:[
        dict(text=w[4],bbox=list(w[:4])) for w in display.page.get_text('words')])
    # The seam-admission rules have their own geometry controls. Here a proved
    # division joins two independently harmless fragments of a damaged word.
    monkeypatch.setattr(vision_pipeline,'_continuation_decision',
        lambda *a:dict(status='joined',reason='synthetic_admitted_seam'))
    with pymupdf.open() as doc:
        for text in ('container coNt-','AiNeR'):
            page=doc.new_page(width=400,height=300);page.insert_text((30,70),text)
        reader=SyntheticReader(
            answer(block('container coNt-',bbox=(0,0,1000,1000),next=True)),
            answer(block('AiNeR',bbox=(0,0,1000,1000),previous=True)))
        destination=tmp_path/'joined-quality.epub'
        report=vision_pipeline.convert(doc,destination,reader)
        assert report['text_quality']['passed']
        assert all(record['source']=='original_source' for record in report['pages'].values())
        failures=[event for event in report['unit_recovery_events']
                  if event['stage']=='compiled_publication_text_quality']
        assert failures and sorted(u['page'] for u in failures[0]['units'])==[0,1]
        with zipfile.ZipFile(destination) as archive:
            assert all('coNtAiNeR' not in archive.read(name).decode()
                for name in archive.namelist() if name.endswith('.xhtml'))


def test_bad_cached_reading_retains_original_image_without_publishing_junk(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    # Avoid depending on a platform OCR spelling: rejection precedes geometry.
    from cps.services.reflow import vision_pixels
    monkeypatch.setattr(vision_pixels,'words',lambda *a,**k:[])
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=300);page.insert_text((30,60),BAD)
        reader=SyntheticReader(answer(block(BAD)))
        destination=tmp_path/'quality.epub'
        report=vision_pipeline.convert(doc,destination,reader)
        assert reader.calls == 1
        assert report['pages'][0]['source']=='original_source'
        assert report['text_quality']['passed']
        assert report['text_quality']['checked_pages']==[0]
        with zipfile.ZipFile(destination) as z:
            body='\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xhtml'))
            assert BAD not in body
            # Authored source control in unit_source.py:125, checked byte-for-byte.
            from tests.fixtures.reflow_output_contracts import assert_authored_substring
            assert_authored_substring(body, '19e518ad9b0a040df5ec9eb3afa961333146f00263fa8d31454ae6e69e1ab2b6', 34)
            assert any(n.endswith('.jpg') for n in z.namelist())


def test_source_script_mismatch_rejects_cached_latin_reading(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import vision_pixels
    monkeypatch.setattr(vision_pixels,'words',lambda *a,**k:[])
    monkeypatch.setattr(vision_pipeline,'clean_native',lambda *a,**k:False)
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_font(fontname='greek',fontfile=None,fontbuffer=pymupdf.Font('helv').buffer)
        # A built-in Unicode font produces genuine non-Latin source codepoints.
        page.insert_text((30,60),'αλφαβήτα γαμμαδέλτα',fontname='greek')
        assert text_quality.script_counts(page.get_text())['GREEK'] >= 4
        reader=SyntheticReader(answer(block(GOOD)))
        report=vision_pipeline.convert(doc,tmp_path/'script.epub',reader)
        assert report['pages'][0]['source']=='original_source'
        assert any('missing_source_script_GREEK' in e['reason'] for e in report['unit_recovery_events'])


def test_cached_non_latin_reading_keeps_printed_script_in_published_text(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import vision_pixels
    monkeypatch.setattr(vision_pipeline,'clean_native',lambda *a,**k:False)
    original='αλφαβήτα γαμμαδέλτα'
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_font(fontname='greek',fontbuffer=pymupdf.Font('helv').buffer)
        page.insert_text((30,60),original,fontname='greek')
        optical=[dict(text=w[4],bbox=list(w[:4])) for w in page.get_text('words')]
        monkeypatch.setattr(vision_pixels,'words',lambda *a,**k:copy.deepcopy(optical))
        reader=SyntheticReader(answer(block(original)))
        destination=tmp_path/'correct-script.epub'
        report=vision_pipeline.convert(doc,destination,reader)
        assert report['pages'][0]['source']=='vision'
        assert report['text_quality']['passed']
        with zipfile.ZipFile(destination) as archive:
            from xml.etree import ElementTree as ET
            reading=' '.join(''.join(ET.fromstring(archive.read(name)).itertext())
                for name in archive.namelist() if name.endswith('.xhtml'))
            assert original in reading


def test_unreliable_layer_is_not_repeated_as_visible_disclosure_prose(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import build_epub
    from cps.services.reflow.source_display import SourceDisplay
    source=GOOD.replace('baskets','baSkEtS').replace('purple','zzqxv')
    words=[dict(text=w,bbox=[20+n*25,30,43+n*25,42]) for n,w in enumerate(source.split())]
    package=build_epub._Package(str(tmp_path/'disclosure.epub'))
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=300)
        try:
            page,report=vision_pipeline.compile_page(answer(block(GOOD)),words,SourceDisplay(doc,0),0,package,{})
            assert report['source_quote_suppressions']
            html='\n'.join(page['body']+page['asides'])
            assert 'baSkEtS' not in html and 'zzqxv' not in html
            assert 'text layer is unreliable' in html
            assert 'Original printed reading' in html
            assert GOOD in text_quality.reading_text(page)
        finally:
            package.abandon()


def test_english_optical_fallback_cannot_erase_a_reader_non_latin_passage(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import build_epub
    from cps.services.reflow.source_display import SourceDisplay
    original=' '.join(['αλφαβήτα','γαμμαδέλτα']*6)
    wrong=' '.join(['blorple','zzqxv']*6)
    optical=[dict(text=w,bbox=[20+n*25,30,43+n*25,42]) for n,w in enumerate(wrong.split())]
    package=build_epub._Package(str(tmp_path/'fallback-script.epub'))
    with pymupdf.open() as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_font(fontname='greek',fontbuffer=pymupdf.Font('helv').buffer)
        page.insert_textbox(pymupdf.Rect(20,20,380,180),original,fontname='greek',fontsize=9)
        assert text_quality.script_counts(page.get_text())['GREEK'] >= 4
        try:
            with pytest.raises(ValueError,match='lost during alignment: GREEK'):
                vision_pipeline.compile_page(answer(block(original)),optical,SourceDisplay(doc,0),0,package,{})
        finally:
            package.abandon()


def test_short_damaged_source_line_is_not_hidden_by_clean_quote_context(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import build_epub
    from cps.services.reflow.source_display import SourceDisplay
    source=(GOOD+' ')*20+'baSKeTs zzqxv green'
    reading=(GOOD+' ')*20+'baskets green green'
    # One short damaged line follows twenty clean lines in the same region.
    # Its two case transitions avoid the unconditional severe-case rule.
    assert not text_quality.inspect(source)['needs_read']
    words=[dict(text=w,bbox=[10+(n%11)*34,15+(n//11)*14,42+(n%11)*34,27+(n//11)*14])
           for n,w in enumerate(source.split())]
    package=build_epub._Package(str(tmp_path/'source-lines.epub'))
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=400)
        try:
            page,report=vision_pipeline.compile_page(answer(block(reading,bbox=(0,0,1000,1000))),
                words,SourceDisplay(doc,0),0,package,{})
            assert report['source_quote_suppressions']
            assert 'internal_case_and_dictionary_misses' in report['source_quote_suppressions'][0]['quality']['reasons']
            assert not any('baSKeTs' in aside or 'zzqxv' in aside for aside in page['asides'])
            assert not text_quality.inspect(text_quality.reading_text(page))['needs_read']
        finally:
            package.abandon()


def test_partial_non_latin_optical_fallback_cannot_hide_behind_a_retained_prefix(tmp_path,monkeypatch):
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:LEXICON)
    from cps.services.reflow import build_epub
    from cps.services.reflow.source_display import SourceDisplay
    original=' '.join(['αλφαβήτα','γαμμαδέλτα']*12)
    partial=' '.join(['αλφαβήτα','γαμμαδέλτα']*4)+' '+GOOD*5
    # The tolerant damaged-source comparison accepts one-third retained script.
    # The compiler must instead preserve the entire accepted reading's letters.
    assert not text_quality.script_mismatches(original,partial)
    assert text_quality.reading_script_mismatches(original,partial)==['GREEK']
    assert not text_quality.reading_script_mismatches(original,original.upper())
    assert not text_quality.inspect(partial)['needs_read']
    words=[dict(text=w,bbox=[10+(n%10)*37,15+(n//10)*14,44+(n%10)*37,27+(n//10)*14])
           for n,w in enumerate(partial.split())]
    package=build_epub._Package(str(tmp_path/'partial-script.epub'))
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=400)
        try:
            with pytest.raises(ValueError,match='lost during alignment: GREEK'):
                vision_pipeline.compile_page(answer(block(original,bbox=(0,0,1000,1000))),
                    words,SourceDisplay(doc,0),0,package,{})
        finally:
            package.abandon()


def test_generated_annotation_exclusion_matches_final_reading_flow():
    from types import SimpleNamespace
    from cps.services.reflow import build_epub,reading_flow
    fragment='<p>coNt<span class="source-reading-annotation">'+(GOOD+' ')*6+'</span>AiNeR</p>'
    before=text_quality.reading_text(dict(body=[fragment],asides=[]))
    assert before.strip()=='coNtAiNeR'
    assert text_quality.inspect(before,lexicon=LEXICON)['needs_read']
    chapters=[build_epub.Chapter(index=1,title='Synthetic',blocks=[fragment],pages=[0])]
    reading_flow.finish(chapters,SimpleNamespace(pages={},figures=[]))
    after=text_quality.reading_text(dict(body=chapters[0].blocks,asides=[]))
    assert before.strip()==after.strip()


def test_table_cell_boundaries_do_not_fuse_separate_words_or_split_inline_letters():
    table='<table><tr><th>GreenBaskets</th><th>PurpleCarts</th><td>FreshBread</td></tr></table>'
    reading=text_quality.reading_text(dict(body=[table],asides=[]))
    assert reading.split()==['GreenBaskets','PurpleCarts','FreshBread']
    assert not text_quality.inspect(reading,lexicon={'greenbaskets','purplecarts','freshbread'})['needs_read']
    inline='<p>co<span>Nt</span>AiNeR</p>'
    reading=text_quality.reading_text(dict(body=[inline],asides=[]))
    assert reading.strip()=='coNtAiNeR'
    assert text_quality.inspect(reading,lexicon={'container'})['needs_read']
