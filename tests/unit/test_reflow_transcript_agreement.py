"""Independent recognition may qualify native text, never replace its words."""
from types import SimpleNamespace as NS
import pytest
from cps.services.reflow import extract


def fixture(text,recognized,score=99):
    spans=[];words=[];x=20
    for value in text.split(' '):
        chars=tuple((i,i+1,x+i*5,30,x+(i+1)*5,40) for i in range(len(value)))
        spans.append(extract.Span(value+' ',10,'Times',0,(x,30,x+len(value)*5,40),40,char_boxes=chars))
        x+=len(value)*5+5
    line=extract.Line(spans,(20,30,x,40))
    raw=extract.RawPage(0,400,600,[extract.Block(0,line.bbox,[line])],[],0)
    for value,box in recognized:words.append(NS(text=value,pdf_bbox=box,confidence=score))
    return raw,NS(words=words)


def test_disagreement_uses_source_word_without_adopting_recognizer_spelling():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('term and ending',[('term',(20,30,40,40)),('und',(45,30,60,40)),('ending',(65,30,95,40))])
    result,report=corroborate(raw,read)
    assert result.text==raw.text
    assert [s.text.strip() for b in result.text_blocks for l in b.lines for s in l.spans if s.transcription_uncertain]==['and']
    assert report['uncertain_words']==1


@pytest.mark.parametrize('native,other',[('O','o'),('5','ō'),('u','ü'),('V','U')])
def test_case_diacritics_and_letter_digit_disagreement_remain_uncertain(native,other):
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture(native,[(other,(20,30,25,40))])
    result,_=corroborate(raw,read)
    assert result.blocks[0].lines[0].spans[0].transcription_uncertain


def test_whitespace_and_ligature_agreement_do_not_rewrite_native():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('ﬁne',[('fi',(20,30,25,40)),('ne',(25,30,35,40))])
    result,report=corroborate(raw,read)
    assert result.text==raw.text and report['uncertain_words']==0


def test_matching_low_confidence_word_is_not_promoted_to_certain():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('term',[('term',(20,30,40,40))],40)
    result,report=corroborate(raw,read)
    assert report['uncertain_words']==1


def test_missing_native_word_requires_bounded_source_region_not_inserted_text():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('term ending',[('term',(20,30,40,40)),('extra',(42,30,60,40)),('ending',(60,30,95,40))])
    result,report=corroborate(raw,read)
    assert result.text==raw.text
    assert result.blocks[0].lines[0].transcription_uncertain


def test_extra_ink_outside_native_lines_is_not_silently_ignored():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('term',[('term',(20,30,40,40)),('omitted',(20,70,55,80))])
    result,report=corroborate(raw,read)
    assert result.transcript_unverified and report['unmatched_words']==1
    assert result.text==raw.text


def test_column_geometry_prevents_neighbor_text_from_corroborating_wrong_word():
    from cps.services.reflow.transcript import corroborate
    raw,read=fixture('and',[('und',(20,30,35,40)),('and',(220,30,235,40))])
    other,_=fixture('and',[])
    from dataclasses import replace
    ln=other.blocks[0].lines[0]
    ln=replace(ln,bbox=(220,30,240,40),spans=[replace(s,bbox=(220,30,235,40),char_boxes=tuple((a,b,x+200,y,xx+200,yy) for a,b,x,y,xx,yy in s.char_boxes)) for s in ln.spans])
    raw.blocks.append(extract.Block(1,ln.bbox,[ln]))
    result,_=corroborate(raw,read)
    assert result.blocks[0].lines[0].spans[0].transcription_uncertain
    assert not result.blocks[1].lines[0].spans[0].transcription_uncertain


def test_atomic_uncertain_note_marker_keeps_link_and_qualified_unresolved_marker():
    from cps.services.reflow import assemble,build_epub
    prose=extract.Span('A complete source sentence ',10,'Times',0,(20,30,160,40),40)
    marker=extract.Span('7',6,'Times',1,(160,27,164,33),33,transcription_uncertain=True)
    line=extract.Line([prose,marker],(20,27,164,40))
    runs=assemble._line_runs(line,0,{7:'note'},set(),[],[],preserve_style=False)
    assert runs[-1]==['sup','7',0,'uncertain']
    html=build_epub._runs_html(runs,{'7'}, {})
    assert 'href="#fn_7"' in html and 'reflow-uncertain' in html
    runs=assemble._line_runs(line,0,{},set(),[],[],preserve_style=False)
    assert runs[-1]==['mark','7',0,'uncertain']
    assert '7 (?)' in build_epub._runs_html(runs,set(),{})


def test_source_recovery_corroborates_hidden_native_without_adopting_ocr(monkeypatch):
    from cps.services.reflow import source,ocr
    from dataclasses import replace
    raw,read=fixture('This is the term and ending in a source sentence. '+ 'The source has a complete sentence with the original words. '*4,[])
    raw.images=[extract.Image((0,0,400,600),1)]
    raw.text_layer_invisible=True
    assert source.needs_recovery(raw)=='' and source.needs_verification(raw)
    assert source.candidates([raw])==[(0,'verify_scan_transcript')]
    words=[]
    for sp in raw.blocks[0].lines[0].spans:
        text=sp.text.strip()
        words.append(ocr.OCRWord('und' if text=='and' else text,sp.bbox,sp.bbox,sp.bbox,99,1,1,1))
    result=ocr.OCRResult('a'*64,0,'engine','eng','identity',300,300,0,0,0,400,600,tuple(words),(),True)
    monkeypatch.setattr(ocr,'_engine',lambda lang:None)
    monkeypatch.setattr(ocr,'recognize_page',lambda *a,**k:result)
    monkeypatch.setattr(extract,'read_page',lambda *a,**k:raw)
    class Page:
        derotation_matrix=(1,0,0,1,0,0)
    recovered=source.recover([Page()],[raw],'a'*64,mode='auto')
    assert recovered.pages[0].text==raw.text
    assert recovered.provenance[0].layer=='native'
    assert recovered.provenance[0].verification['uncertain_words']==1
    assert recovered.attempted==recovered.reused==1 and recovered.ocr_pages==0
    assert recovered.summary()['pages'][0]['verification']


def test_disabled_verification_preserves_source_and_visible_native_needs_no_recognition(monkeypatch):
    from cps.services.reflow import source,skeleton
    raw,read=fixture('This is complete source prose with a normal sentence.',[])
    raw.images=[extract.Image((0,0,400,600),1)];raw.text_layer_invisible=True
    recovered=source.recover([], [raw], 'a'*64,mode='off')
    assert recovered.pages[0].transcript_unverified
    skel=skeleton.page_skeleton(recovered.pages[0],skeleton.book_style([raw]))
    assert any(r.kind=='figure' and r.bbox==(0,0,400,600) for r in skel.regions)
    raw.text_layer_invisible=False
    assert not source.needs_verification(raw)
    recovered=source.recover([], [raw], 'a'*64,mode='auto')
    assert recovered.pages[0] is raw and recovered.attempted==0


def test_transcript_source_word_builds_with_original_link_and_internal_validation(tmp_path):
    import pymupdf,zipfile
    from cps.services.reflow import assemble,skeleton,build_epub
    from cps.services.reflow.native_text import descriptor
    doc=pymupdf.open();page=doc.new_page();page.insert_text((40,50),'printed source')
    record=descriptor(0,(40,38,100,52),12,'Times',reason='transcript')
    element=assemble.Element('p',pno=0,bbox=(40,38,100,52),runs=[['t','Before '],['glyph','untrusted',record],['t',' after.']])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=12))
    target=tmp_path/'transcript.epub';build_epub.build(book,str(target),doc=doc)
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as archive:
        chapters=[archive.read(name).decode() for name in archive.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')]
        html=''.join(chapters)
        assert 'untrusted' not in html and 'transcription uncertain' in html
        assert 'original-p0000.xhtml#page' in html and '<img ' in html
    doc.close()


def test_unavailable_optional_recognizer_fails_closed_for_hidden_source(monkeypatch):
    from cps.services.reflow import source,ocr
    raw,_=fixture('The source has a complete sentence with the original words. '*4,[])
    raw.images=[extract.Image((0,0,400,600),1)];raw.text_layer_invisible=True
    def absent(*a):raise ocr.OCRUnavailable('unavailable')
    monkeypatch.setattr(ocr,'_engine',absent)
    recovered=source.recover([], [raw], 'a'*64,mode='auto_if_available')
    assert recovered.engine_unavailable and recovered.failed==1
    assert recovered.pages[0].transcript_unverified and recovered.pages[0].text==raw.text


@pytest.mark.parametrize('atom',['glyph','raised','sup'])
def test_marker_repair_cannot_reach_across_an_immutable_source_atom(atom):
    from cps.services.reflow import assemble
    from cps.services.reflow.native_text import descriptor
    run=[atom,"following.'",descriptor(0,(20,20,60,30),10,'Times',reason='transcript')] if atom=='glyph' else [atom,'7',0]
    runs=[['t','A complete original word zodiacal ', 'italic'],run]
    before=__import__('copy').deepcopy(runs);repairs=[]
    assemble._strip_marker_prefix(runs,repairs,0,'36',136)
    assert runs==before and repairs==[]
