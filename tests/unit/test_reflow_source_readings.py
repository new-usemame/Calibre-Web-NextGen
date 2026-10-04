"""New readings annotate exact retained occurrences; they never replace source."""
import copy
import json
import math
import hashlib
import zipfile
from dataclasses import replace
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import assemble,extract,skeleton,enriched_source,build_epub
from cps.services.reflow.structural_ops import ContractError

pytestmark=pytest.mark.unit

@pytest.fixture
def reading_source(tmp_path):
    path=tmp_path/'source.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=500,height=700)
        for i in range(12):p.insert_text((50,70+30*i),'Equal 15 & original token.',fontsize=12)
        pdf.save(path)
    with pymupdf.open(path) as doc:
        raw=extract.read_page(doc,0)
        regions=[skeleton.Region('body',list(b.lines),bbox=b.bbox) for b in raw.text_blocks]
        book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,regions)],skeleton.BookStyle(12),[raw])
        source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        yield book,doc,source,raw,tmp_path

def selectors(raw):
    return [dict(block=bi,line=li,span=si,start=sp.text.index('15'),end=sp.text.index('15')+2,original='15')
            for bi,b in enumerate(raw.blocks) for li,l in enumerate(b.lines) for si,sp in enumerate(l.spans) if '15' in sp.text]

def evidence(f,chosen=None,confidence=.96):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,_=f
    prepared=readings.prepare(book,doc,source,raw,chosen or selectors(raw),synthetic=True)
    recognition=dict(snapshot=prepared.snapshot,observation='synthetic-recognition',readings=[dict(occurrence=o['id'],alternatives=[dict(text='25',confidence=confidence)]) for o in prepared.request()['occurrences']])
    request=readings.review_request(prepared,recognition)
    review=dict(snapshot=request['snapshot'],observation='synthetic-independent-review',accept=True,problems=[])
    admitted=readings.admit(book,doc,source,raw,prepared,recognition,review)
    return prepared,recognition,review,admitted

def test_exact_duplicate_occurrences_reissue_source_without_rewriting(reading_source):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,_=reading_source
    prepared,_,_,admitted=evidence(reading_source,[selectors(raw)[-1]])
    enriched=readings.attach(book,source,admitted)
    assert enriched.html==source.html and enriched.provenance_json==source.provenance_json and enriched.records_json==source.records_json
    assert enriched.identity!=source.identity
    record=enriched.report()['source_readings']['entries'][0]
    assert record['original']=='15' and record['alternatives']==[dict(text='25',confidence=.96)]
    assert record['occurrence']['block']==selectors(raw)[-1]['block']
    assert record['inline_placed'] is False
    with pytest.raises(ContractError):readings.attach(book,source,copy.deepcopy(admitted))

@pytest.mark.parametrize('damage',['original','offset','duplicate','overlap','raw','nan','low','high','infinite','review','raster'])
def test_reading_evidence_fails_closed(reading_source,damage):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,_=reading_source
    if damage in ('original','offset','duplicate','overlap'):
        chosen=[selectors(raw)[0]]
        if damage=='original':chosen[0]['original']='25'
        elif damage=='offset':chosen[0]['start']+=1
        else:chosen.append(dict(chosen[0]))
        with pytest.raises(ContractError):readings.prepare(book,doc,source,raw,chosen,synthetic=True)
        return
    prepared,recognition,review,_=evidence(reading_source)
    if damage=='raw':raw.blocks[0].lines[0].spans[0].text+='changed'
    elif damage in ('nan','low','high','infinite'):recognition['readings'][0]['alternatives'][0]['confidence']={'nan':math.nan,'low':.89,'high':1.01,'infinite':math.inf}[damage]
    elif damage=='review':review['snapshot']='stale'
    else:prepared=replace(prepared,raster=b'corrupt')
    with pytest.raises(ContractError):readings.admit(book,doc,source,raw,prepared,recognition,review)


def test_every_alternative_has_visible_crop_heading_and_return_after_tenth(reading_source):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,tmp=reading_source
    _,_,_,admitted=evidence(reading_source)
    enriched=readings.attach(book,source,admitted)
    build_epub.build(book,str(tmp/'unbound.epub'),doc=doc,source_pages={0:source},identifier='reading-fixture')
    built=build_epub.build(book,str(tmp/'readings.epub'),doc=doc,source_pages={0:enriched},identifier='reading-fixture')
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        x='{http://www.w3.org/1999/xhtml}'
        root=ET.fromstring(z.read('OEBPS/original-p0000.xhtml'))
        headings=[n for n in root.iter(x+'h2') if n.get('id','').startswith('reading-')]
        assert len(headings)==12
        for n in headings:
            section=next(s for s in root.iter() if n in list(s))
            text=''.join(section.itertext())
            assert 'Original: 15' in text and 'Proposed reading: 25' in text and 'Synthetic' in text
            assert any(a.get('href','').endswith('#'+n.get('id')) for a in root.iter(x+'a'))
            assert any('Return' in ''.join(a.itertext()) for a in section.iter(x+'a'))
            crop=z.read('OEBPS/'+section.find(x+'img').get('src'))
            entry=next(e for e in enriched.report()['source_readings']['entries'] if e['id']==n.get('id'))
            assert hashlib.sha256(crop).hexdigest()==entry['occurrence']['crop_sha256']
            for link in section.iter(x+'a'):
                if 'Return' in ''.join(link.itertext()):
                    filename,fragment=link.get('href').split('#')
                    target=ET.fromstring(z.read('OEBPS/'+filename))
                    assert len([e for e in target.iter() if e.get('id')==fragment])==1

def test_low_confidence_is_uncertainty_without_guess_and_review_is_independent(reading_source):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,_=reading_source
    prepared,recognition,review,_=evidence(reading_source,[selectors(raw)[0]])
    recognition['readings'][0]['alternatives']=[]
    review['snapshot']=readings.review_request(prepared,recognition)['snapshot']
    result=readings.attach(book,source,readings.admit(book,doc,source,raw,prepared,recognition,review))
    assert result.report()['source_readings']['entries'][0]['alternatives']==[]
    review['observation']=recognition['observation']
    with pytest.raises(ContractError):readings.admit(book,doc,source,raw,prepared,recognition,review)


def test_stale_crop_frame_and_serialized_source_cannot_issue_authority(reading_source):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,tmp=reading_source
    prepared,recognition,review,admitted=evidence(reading_source,[selectors(raw)[0]])
    enriched=readings.attach(book,source,admitted)
    forged=replace(enriched,report_json=enriched.report_json.replace('25','26'))
    with pytest.raises(ContractError):build_epub.build(book,str(tmp/'forged.epub'),doc=doc,source_pages={0:forged})
    with pytest.raises(ContractError):readings.admit(book,doc,source,raw,replace(prepared,crops=(b'tampered',)),recognition,review)
    doc[0].set_cropbox(pymupdf.Rect(10,10,490,690))
    with pytest.raises(ContractError):readings.prepare(book,doc,source,raw,[selectors(raw)[0]],synthetic=True)


def test_native_child_replays_readings_and_rejects_parent_forgery(tmp_path):
    from cps.services.reflow import source_readings as readings,layout_ops,layout_build
    from cps.services.reflow.native_ipc import NativeDocument
    from tests.unit.test_reflow_layout_ops import answer
    path=tmp_path/'native.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=500,height=700);p.insert_text((50,100),'Equal 15 & original token.',fontsize=12);pdf.save(path)
    with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
        result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
        book=result.book;raw=result.raw_pages[0];base=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        _,_,_,admitted=evidence((book,doc,base,raw,tmp_path))
        source=readings.attach(book,base,admitted)
        prepared=layout_ops.prepare(book,doc,0,source,raw)
        plan=prepared.accept(book,doc,answer(prepared),source_page=source,raw_page=raw)
        assert layout_build.figure_failures(book,doc,[plan],{0:source},{0:raw})=={}
        build_epub.build(book,str(tmp_path/'unbound.epub'),doc=doc,source_pages={0:base},identifier='reading-fixture')
        built=build_epub.build(book,str(tmp_path/'native-readings.epub'),doc=doc,source_pages={0:source},layout_plans=[plan],raw_pages={0:raw},identifier='reading-fixture')
        assert build_epub.validate(built.path)==[]
        altered=copy.deepcopy(book);altered.pages[0][0].runs[0][1]+=' fabricated'
        fake=enriched_source.prepare_source_page(altered,0,{'layer':'native'})
        with pytest.raises((ValueError,ContractError)):readings.prepare(altered,doc,fake,raw,selectors(raw),synthetic=True)
        # A transported, resealed public digest still cannot replace replayed evidence.
        report=source.report();report['source_readings']['proof']['binding']['occurrences'][0]['crop_sha256']='0'*64
        fake=replace(source,report_json=json.dumps(report));fake=replace(fake,seal=fake._identity())
        with pytest.raises((ValueError,ContractError)):build_epub.build(book,str(tmp_path/'fake.epub'),doc=doc,source_pages={0:fake})
    with zipfile.ZipFile(built.path) as z:
        assert b'Proposed reading: 25' in z.read('OEBPS/original-p0000.xhtml')


def test_entity_original_and_unicode_candidate_remain_literal(reading_source):
    from cps.services.reflow import source_readings as readings
    book,doc,source,raw,_=reading_source
    item=selectors(raw)[0];span=raw.blocks[item['block']].lines[item['line']].spans[item['span']]
    item.update(start=span.text.index('&'),end=span.text.index('&')+1,original='&')
    prepared=readings.prepare(book,doc,source,raw,[item],synthetic=True)
    recognition=dict(snapshot=prepared.snapshot,observation='first',readings=[dict(occurrence=prepared.request()['occurrences'][0]['id'],alternatives=[dict(text='＆',confidence=.9)])])
    review=dict(snapshot=readings.review_request(prepared,recognition)['snapshot'],observation='second',accept=True,problems=[])
    result=readings.attach(book,source,readings.admit(book,doc,source,raw,prepared,recognition,review))
    assert result.html==source.html and '&amp;' in result.html
    entry=result.report()['source_readings']['entries'][0]
    assert entry['original']=='&' and entry['alternatives'][0]['text']=='＆'
