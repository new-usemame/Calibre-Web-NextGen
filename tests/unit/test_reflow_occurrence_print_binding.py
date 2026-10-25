"""Synthetic sequence ownership keeps a plausible reading's print honest."""
import io
import pymupdf
import pytest
from lxml import etree
from PIL import Image
from cps.services.reflow import inline_print,vision_pipeline as vp,reading_tiers
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit

class Package:
    def __init__(self):self.images={}
    def image(self,name,data):
        assert name not in self.images
        self.images[name]=data


def published(display,words,reading,wrong,*,heading=False):
    package=Package();images={'images/old.jpg':dict(kind='vision_disclosure',bbox=wrong)};extras=[]
    text=reading.replace('garden',reading_tiers.flagged_html('garden','images/old.jpg','test-print'))
    text+='<a id="r-test-print" class="source-disclosure-ref" epub:type="noteref" href="#test-print"><sup>†</sup></a>'
    body,supplement=vp._present_flagged_prints(text,display,0,'sample',[wrong],package,images,extras,
        evidence_words=words,heading=heading)
    return body,supplement,package,images,extras


def test_shared_evidence_uses_its_unique_reading_not_a_wrong_row(monkeypatch):
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=230)
        p.insert_text((25,55),'The blue garden stays green beside the quiet path.',fontsize=12)
        p.insert_text((25,155),'The red tractor waits beside the old barn.',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        monkeypatch.setattr(inline_print,'preview',lambda *a:None)
        _,supplement,package,images,_=published(SourceDisplay(doc,0),words,
            'The blue garden stays green beside the quiet path.',[20,140,340,160])
        asset=next(v for v in images.values()if v.get('kind')=='flagged_unit_preview')
        assert asset['bbox'][3]<80
        assert asset['placement_method']=='unique_sequence_source_context'
        root=etree.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+supplement+'</div>')
        assert len(root.xpath('.//img'))==1


def test_ambiguous_reading_gets_complete_page_context_not_a_guessed_word(monkeypatch):
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=230)
        for y in(55,155):p.insert_text((25,y),'blue garden',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        _,_,_,images,_=published(SourceDisplay(doc,0),words,'blue garden',[20,140,140,160])
        asset=next(v for v in images.values()if v.get('kind')=='flagged_unit_preview')
        assert asset['bbox']==list(p.rect)
        assert asset['unplaced_context']


def test_wrong_inline_preview_is_rebound_before_it_can_stay_inline():
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=230)
        reading='The blue garden stays green beside the quiet path.'
        p.insert_text((25,55),reading,fontsize=12)
        p.insert_text((25,155),'The red tractor waits beside the old barn.',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        package=Package();images={'images/old.png':dict(kind='inline_print_preview',bbox=[20,140,60,160])}
        text=reading.replace('garden',reading_tiers.flagged_html('garden','images/old.png','test-print','height:9em'))
        body,_,=vp._present_flagged_prints(text,SourceDisplay(doc,0),0,'sample',[[20,140,340,160]],package,images,[],evidence_words=words)
        image=etree.fromstring('<div>'+body+'</div>').xpath('.//img')[0]
        assert image.get('src')!='images/old.png'
        assert images[image.get('src')]['bbox'][3]<80
        assert 'height:1.10000em'in image.get('style')


def test_heading_does_not_repeat_its_words_as_an_inline_or_block_print():
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=200);p.insert_text((25,60),'The blue garden stays green.',fontsize=14)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        body,supplement,_,_,extras=published(SourceDisplay(doc,0),words,'The blue garden stays green.',[20,45,350,70],heading=True)
        assert '<img'not in body+supplement
        assert 'reflow-plausible'in body and extras


def test_blank_and_impossibly_thin_print_cannot_carry_a_multi_letter_reading():
    for draw in('blank','vertical','horizontal'):
        image=Image.new('L',(80,50),255)
        if draw!='blank':
            from PIL import ImageDraw
            box=(38,5,40,40)if draw=='vertical'else(2,25,78,26)
            ImageDraw.Draw(image).rectangle(box,fill=0)
        out=io.BytesIO();image.save(out,format='PNG')
        assert inline_print.readable_preview((out.getvalue(),{}),'garden')is None


def test_compiler_replaces_stale_flag_crop_at_its_own_occurrence(tmp_path,monkeypatch):
    """The publisher supplies real source words to placement, not just hints."""
    from cps.services.reflow import pixel_adjudication as pa
    from tests.unit.test_reflow_vision_pilot import answer,block
    reading='The blue garden stays green beside the quiet path.'
    with pymupdf.open()as drawing,pymupdf.open()as scan:
        p=drawing.new_page(width=400,height=230)
        p.insert_text((25,55),reading,fontsize=12)
        p.insert_text((25,155),'The red tractor waits beside the old barn.',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in p.get_text('words')]
        page=scan.new_page(width=400,height=230);page.insert_image(page.rect,pixmap=p.get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        original=pa.adjudicate
        def unsettled(*a,**kw):
            result=original(*a,**kw);index=result['tokens'].index('garden')
            result['marked'].add(index);result['boxes'][index]=[20,140,340,160]
            result.setdefault('word_notes',{})[index]=dict(status='UNRESOLVED',
                source=['garden'],model=['garden'],chosen=['garden'],
                method='synthetic_uncertain_word',source_range=[index,index+1],model_range=[index,index+1])
            return result
        monkeypatch.setattr(pa,'adjudicate',unsettled)
        package=Package();assets={}
        compiled,receipt=vp.compile_page(answer(block(reading)),words,SourceDisplay(scan,0),0,
            package,assets,pixel_words=words,regions={'b1':[[20,40,390,70]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    root=etree.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body']+compiled['asides'])+'</div>')
    flags=root.xpath('.//span[@class="reflow-flagged-reading"]')
    assert len(flags)==1 and ''.join(flags[0].xpath('.//span[@class="reflow-plausible"]')[0].itertext())=='garden'
    shown=flags[0].xpath('.//img')
    assert shown and assets[shown[0].get('src')]['bbox'][3]<80
    link=flags[0].xpath('.//a')[0].get('href')[1:]
    inspected=root.xpath('//*[@id=$target]//img',target=link)
    assert inspected and assets[inspected[0].get('src')]['bbox'][3]<80
    assert all(image.get('src')in package.images for image in root.xpath('.//img'))
    assert receipt['blocks'][0]['published_tokens']==reading.split()


def test_relocated_inspection_does_not_rewrite_a_sibling_note_in_the_same_fragment():
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=230)
        reading='The blue garden stays green beside the quiet path.'
        p.insert_text((25,55),reading,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        images={'images/old.png':dict(kind='inline_print_preview',bbox=[20,140,60,160])}
        extras=['<aside id="test-print"><p>Own detail</p><img src="images/old.png"/></aside>'
            '<aside id="other-source"><p>Separate source</p><img src="images/other.jpg"/></aside>']
        text=reading.replace('garden',reading_tiers.flagged_html('garden','images/old.png','test-print','height:9em'))
        vp._present_flagged_prints(text,SourceDisplay(doc,0),0,'sample',[[20,140,340,160]],Package(),images,extras,evidence_words=words)
    root=etree.fromstring('<div>'+extras[0]+'</div>')
    assert root.xpath('.//aside[@id="test-print"]/img')[0].get('src')!='images/old.png'
    assert root.xpath('.//aside[@id="other-source"]/img')[0].get('src')=='images/other.jpg'


def test_word_relocation_preserves_the_complete_inspection_shared_by_a_typed_reference():
    with pymupdf.open()as doc:
        p=doc.new_page(width=400,height=230)
        reading='The blue garden stays green beside the quiet path.'
        p.insert_text((25,55),reading,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in p.get_text('words')]
        images={'images/old.png':dict(kind='inline_print_preview',bbox=[20,140,60,160])}
        extras=['<aside id="test-print"><p>Shared original detail</p><img src="images/old.png"/></aside>']
        text=reading.replace('garden',reading_tiers.flagged_html('garden','images/old.png','test-print','height:9em'))
        vp._present_flagged_prints(text,SourceDisplay(doc,0),0,'sample',[[20,140,340,160]],Package(),images,extras,
            evidence_words=words,protected_inspections={'test-print'})
    root=etree.fromstring('<div>'+extras[0]+'</div>')
    shown=root.xpath('.//aside[@id="test-print"]/img')
    assert len(shown)==2
    assert images[shown[0].get('src')]['bbox'][3]<80
    assert shown[1].get('src')=='images/old.png'


def test_compiler_preserves_the_admitted_marker_image_when_relocating_its_neighbor(tmp_path,monkeypatch):
    from cps.services.reflow import pixel_adjudication as pa
    from tests.unit.test_reflow_vision_pilot import block
    body='The blue garden <n:n1> stays beside the quiet path.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=300)
        page.insert_text((20,60),body.replace(' <n:n1>',''),fontsize=12)
        body_words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        garden=body_words[2]['bbox'];marker=[garden[2]+.2,garden[1],garden[2]+4,garden[1]+6]
        page.insert_text((marker[0],marker[3]),'1',fontsize=6)
        page.insert_text((20,170),'1 A short note stays readable.',fontsize=10)
        notes=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')if w[1]>120]
        words=body_words+notes
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        original=pa.adjudicate
        def unsettled(*a,**kw):
            result=original(*a,**kw)
            if 'garden'in result['tokens']:
                i=result['tokens'].index('garden');result['marked'].add(i)
                result['boxes'][i]=[15,40,390,80]
                result.setdefault('word_notes',{})[i]=dict(status='UNRESOLVED',
                    source=['garden'],model=['garden'],chosen=['garden'],method='synthetic_uncertain_word')
            return result
        monkeypatch.setattr(pa,'adjudicate',unsettled)
        monkeypatch.setattr(vp,'_literal_note_refs',lambda *a,**kw:{})
        def proof(tokens,*a,**kw):
            return ({tokens.index('<n:n1>'):dict(status='UNPROVED',source_bbox=marker,
                reason='synthetic_ambiguous_reference')}if '<n:n1>'in tokens else{})
        monkeypatch.setattr(vp,'_placeholder_display_evidence',proof)
        context=dict(cache=tmp_path/'cache',labels={'1':[(0,'n1')]},owners={},
            catalogue={(0,'n1'):{'target':'v4-note-0-n1','label':'1'}},refs={})
        package=Package();assets={}
        compiled,receipt=vp.compile_page({'blocks':[
            block(body,bbox=(0,0,1000,330)),
            block('A short note stays readable.',ident='n1',role='footnote',marker='1',bbox=(0,400,1000,1000))]},
            words,SourceDisplay(doc,0),0,package,assets,pixel_words=words,note_context=context)
    shared=[r for r in receipt['note_marker_disclosures']if r.get('shared_existing_source_disclosure')]
    assert len(shared)==1
    root=etree.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body']+compiled['asides'])+'</div>')
    admitted=root.xpath('//*[@id=$target]//img',target=shared[0]['source_reference'])
    assert any(image.get('src')==shared[0]['source_image']for image in admitted)
    assert any(image.get('src')!=shared[0]['source_image']for image in admitted)


def test_shared_page_preview_cannot_relabel_an_admitted_source_receipt(monkeypatch):
    class SharedPackage(Package):
        def __init__(self):super().__init__();self.aliases={}
        def disclosure_image(self,name,display,bounds):
            assert list(bounds)==list(display.rect)
            self.aliases[name]='images/admitted.jpg'
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=230)
        page.insert_text((25,55),'blue garden',fontsize=12)
        page.insert_text((25,155),'blue garden',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
        images={'images/admitted.jpg':dict(page=0,bbox=list(page.rect),kind='vision_disclosure')}
        text=reading_tiers.flagged_html('garden','images/admitted.jpg','shared-detail')
        extras=['<aside id="shared-detail"><img src="images/admitted.jpg"/></aside>']
        vp._present_flagged_prints(text,SourceDisplay(doc,0),0,'sample',[list(page.rect)],
            SharedPackage(),images,extras,evidence_words=words,protected_inspections={'shared-detail'})
    assert list(images)==['images/admitted.jpg']
    assert images['images/admitted.jpg']['kind']=='vision_disclosure'
    uses=images['images/admitted.jpg']['presentation_uses']
    assert len(uses)==2
    assert uses[0]['presentation_role']=='located_inspection_context'
    assert uses[1]['kind']=='flagged_unit_preview'
