"""Retained split labels and flag/segmentation drift retain exact originals."""
import copy
import json
from dataclasses import asdict,replace
import pymupdf
import pytest
from cps.services.reflow import assemble,extract,skeleton,enriched_source,source_readings as readings,build_epub,source_inventory
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_source_readings import evidence

pytestmark=pytest.mark.unit

@pytest.fixture
def retained(tmp_path):
    path=tmp_path/'split.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=400);p.insert_text((40,70),'2°1 Example 15',fontsize=12);pdf.save(path)
    with pymupdf.open(path) as doc:
        raw=extract.read_page(doc,0,keep_char_boxes=True)
        span=raw.blocks[0].lines[0].spans[0]
        pieces=[]
        for start,end in [(0,1),(1,2),(2,3),(3,len(span.text))]:
            chars=[c for c in span.char_boxes if start<=c[0] and c[1]<=end]
            box=tuple([min(c[2] for c in chars),min(c[3] for c in chars),max(c[4] for c in chars),max(c[5] for c in chars)])
            pieces.append(replace(span,text=span.text[start:end],bbox=box,char_boxes=(),transcription_uncertain=start==1))
        raw.blocks[0].lines[0].spans=pieces
        book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,[skeleton.Region('body',raw.blocks[0].lines,bbox=raw.blocks[0].bbox)])],skeleton.BookStyle(12),[raw])
        source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        yield book,doc,source,raw,tmp_path

def selected():
    return dict(original='2°1',segments=[dict(block=0,line=0,span=i,start=0,end=1) for i in range(3)])

def test_multispan_replay_and_publication_preserve_retained_source(retained):
    book,doc,base,raw,tmp=retained;before=copy.deepcopy(asdict(raw))
    prepared,recognition,review,admitted=evidence(retained,[selected()])
    source=readings.attach(book,base,admitted)
    assert source.html==base.html and source.records_json==base.records_json and source.provenance_json==base.provenance_json
    assert asdict(raw)==before
    occurrence=prepared.request()['occurrences'][0]
    assert occurrence['original']=='2°1' and len(occurrence['segments'])==3
    assert len(occurrence['character_mapping'])==3
    assert readings.replay(book,doc,base,raw,source).identity==source.identity
    built=build_epub.build(book,str(tmp/'segments.epub'),doc=doc,source_pages={0:source},raw_pages={0:raw})
    assert build_epub.validate(built.path)==[]
    # Direct replay must use retained raw, never freshly normalized raw.
    with pytest.raises((ValueError,ContractError)):
        build_epub.build(book,str(tmp/'missing-retained.epub'),doc=doc,source_pages={0:source})

@pytest.mark.parametrize('damage',['reverse','skip','partial','duplicate','overlap','original','geometry','ambiguous','raw_flag','pdf'])
def test_segment_binding_fails_closed(retained,damage):
    book,doc,base,raw,tmp=retained;item=selected()
    if damage in ('raw_flag','pdf'):
        prepared,recognition,review,_=evidence(retained,[item])
        if damage=='raw_flag':raw.blocks[0].lines[0].spans[1].transcription_uncertain=False
        else:doc[0].insert_text((40,100),'changed')
        with pytest.raises(ContractError):readings.admit(book,doc,base,raw,prepared,recognition,review)
        return
    chosen=[item]
    if damage=='reverse':item['segments'].reverse()
    elif damage=='skip':item['segments'].pop(1)
    elif damage=='partial':item['segments'][0]['end']=0
    elif damage=='original':item['original']='201'
    elif damage=='duplicate':chosen.append(copy.deepcopy(item))
    elif damage=='overlap':chosen.append(dict(block=0,line=0,span=1,start=0,end=1,original='°'))
    else:
        if damage=='geometry':raw.blocks[0].lines[0].spans[1].bbox=(0,0,1,1)
        else:raw.blocks[0].lines.append(copy.deepcopy(raw.blocks[0].lines[0]))
        # Even reassembled caller data must have a unique exact native geometry mapping.
        book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,[skeleton.Region('body',raw.blocks[0].lines,bbox=raw.blocks[0].bbox)])],skeleton.BookStyle(12),[raw])
        base=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    with pytest.raises(ContractError):readings.prepare(book,doc,base,raw,chosen,synthetic=True)

def test_native_child_reissues_multispan_occurrence(tmp_path):
    from cps.services.reflow.native_ipc import NativeDocument
    from cps.services.reflow import layout_ops,layout_build
    from tests.unit.test_reflow_layout_ops import answer
    path=tmp_path/'native-segments.pdf'
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=400,height=400)
        page.insert_text((40,70),'2',fontname='helv',fontsize=12)
        page.insert_text((47,70),'o',fontname='cour',fontsize=12)
        page.insert_text((54.2,70),'1',fontname='helv',fontsize=12)
        pdf.save(path)
    with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
        result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
        book=result.book;raw=result.raw_pages[0]
        base=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        line=raw.blocks[0].lines[0]
        assert [s.text for s in line.spans]==['2','o','1']
        item=selected();item['original']='2o1'
        prepared,recognition,review,admitted=evidence((book,doc,base,raw,tmp_path),[item])
        source=readings.attach(book,base,admitted)
        layout=layout_ops.prepare(book,doc,0,source,raw)
        plan=layout.accept(book,doc,answer(layout),source_page=source,raw_page=raw)
        assert layout_build.figure_failures(book,doc,[plan],{0:source},{0:raw})=={}
        built=build_epub.build(book,str(tmp_path/'native-segments.epub'),doc=doc,source_pages={0:source},raw_pages={0:raw},layout_plans=[plan])
        assert build_epub.validate(built.path)==[]
        old_layout=layout_ops.prepare(book,doc,0,base,raw)
        with pytest.raises((ValueError,ContractError)):
            old_layout.accept(book,doc,answer(old_layout),source_page=source,raw_page=raw)


def test_layout_pipeline_replays_retained_readings_before_preparing_requests(retained):
    from types import SimpleNamespace
    from cps.services.reflow import layout_pipeline
    book,doc,base,raw,tmp=retained
    _,_,_,admitted=evidence(retained,[selected()])
    annotated=readings.attach(book,base,admitted)
    result=SimpleNamespace(book=book,fingerprint=extract.document_fingerprint(doc),
        raw_pages=[raw],recovery=None,page_html={0:base.html},source_pages={0:annotated})
    seen=[]
    layout_pipeline.run_layout(doc,prepared_result=result,page_numbers=[0],
        prepared_observer=lambda b,p,s:seen.append((p.source_identity,s.identity)))
    assert result.source_pages[0].identity==annotated.identity
    assert seen==[(annotated.identity,annotated.identity)]
    assert result.source_pages[0].report()['source_readings']==annotated.report()['source_readings']
