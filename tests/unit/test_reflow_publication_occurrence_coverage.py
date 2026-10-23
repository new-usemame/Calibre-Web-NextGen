"""Synthetic admitted occurrences cannot disappear in an optical fallback."""
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


def test_optical_fallback_retains_unmatched_original_local_occurrences_as_print(tmp_path,monkeypatch):
    text='The opening words remain beside the quiet window.'
    with pymupdf.open()as doc:
        page=doc.new_page(width=450,height=180)
        page.insert_text((20,60),text,fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=96)for w in page.get_text('words')]
        optical=source[2:]
        original_align=vp.vision_reading.align
        def align(text,words,low,**kwargs):
            result=original_align(text,words,low,**kwargs)
            if kwargs.get('has_layer'):
                result.update(tokens=[w['text']for w in words],fallback=True,
                    boxes={i:w['bbox']for i,w in enumerate(words)},marked=set(range(len(words))))
            return result
        monkeypatch.setattr(vp.vision_reading,'align',align)
        monkeypatch.setattr(pa,'read_crop',lambda *args:optical)
        class Package:
            def image(self,*args):pass
        images={}
        page,record=vp.compile_page(answer(block(text,bbox=(0,0,1000,1000))),[],
            SourceDisplay(doc,0),0,Package(),images,pixel_words=optical,
            forced_uncertain={'b1'},local_ocr=True,
            local_ocr_units=[dict(id='b1',words=source,source_word_indices=list(range(len(source))))],
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    # The already published complete local source page is sufficient; it must
    # be named by the occurrence ledger, without inventing a second disclosure.
    assert 'v4-local-ocr-source-0' in ''.join(page['asides'])
    coverage=record['blocks'][0]['occurrence_coverage']
    assert len(coverage)==len(source)
    assert [r['disposition']for r in coverage[:2]]==['print','print']
    assert all(r['disposition']in('live','print')for r in coverage)
    assert all(r.get('image')in images for r in coverage[:2])
    assert all(images[r['image']]['bbox'][0]<=r['source_bbox'][0]
        and images[r['image']]['bbox'][2]>=r['source_bbox'][2]for r in coverage[:2])
    assert record['occurrence_coverage']['missing']==0
    assert record['occurrence_coverage']['inventory']==len(source)


def test_masked_evidence_envelope_cannot_claim_the_erased_middle_occurrence():
    from cps.services.reflow import occurrence_coverage as coverage
    words=[dict(text='sample',bbox=[40,20,60,32])]
    notes={0:dict(source=['sample'],source_image_only=True)}
    images={'images/parts.jpg':dict(page=0,bbox=[0,20,100,32],
        source_parts=[dict(bbox=[0,20,20,32]),dict(bbox=[80,20,100,32])])}
    rows=coverage.classify(words,['sample'],{0:[40,20,60,32]},notes,images,0)
    assert rows[0]['disposition']=='missing'


def test_equal_spelling_at_a_remote_occurrence_cannot_prove_live_coverage():
    from cps.services.reflow import occurrence_coverage as coverage
    words=[dict(text='sample',bbox=[40,20,60,32])]
    rows=coverage.classify(words,['sample'],{0:[40,100,60,112]},{},{},0)
    assert coverage.summary(rows)==dict(inventory=1,live=0,print=0,missing=1)


def test_final_publication_checks_actual_text_after_compiler_coverage(tmp_path,monkeypatch):
    from tests.unit.test_round4_defects import InkPage,convert_ink
    page=InkPage();page.text('The quiet window remains open.', 'b1')
    compile_page=vp.compile_page
    def omit_after_compilation(*args,**kwargs):
        page,record=compile_page(*args,**kwargs)
        page['body']=[text.replace('quiet ','')for text in page['body']]
        return page,record
    monkeypatch.setattr(vp,'compile_page',omit_after_compilation)
    _,result,docs=convert_ink(tmp_path,monkeypatch,[page])
    coverage=result['publication_coverage']
    assert coverage['totals']['missing']==0
    lost=next(row for row in coverage['pages'][0]['rows']if row['source_text']=='quiet')
    assert lost['disposition']=='print'
    assert lost['kind']=='publication_coverage_source_context'
    assert lost['image']in result['images']
    assert any(node.get('src')==lost['image']for root in docs.values()for node in root.iter())


def test_final_publication_preserves_unmarked_complete_live_reading(tmp_path,monkeypatch):
    from tests.unit.test_round4_defects import InkPage,convert_ink
    page=InkPage();page.text('The quiet window remains open.', 'b1')
    _,result,_=convert_ink(tmp_path,monkeypatch,[page])
    coverage=result['publication_coverage']['totals']
    assert coverage==dict(inventory=5,live=5,print=0,missing=0,excluded=0)
    assert not any(image.get('block')=='publication-coverage'for image in result['images'].values())


@pytest.mark.parametrize('resource_exists',[True,False])
def test_coverage_resolves_only_aliases_with_an_actual_published_resource(monkeypatch,resource_exists):
    from cps.services.reflow import occurrence_coverage as coverage,unit_source
    from types import SimpleNamespace
    alias='images/shared-page.jpg';encoded='images/encoded-page.png'
    package=SimpleNamespace(images={encoded:42}if resource_exists else{},aliases={alias:encoded})
    def needs_repair(*args,**kwargs):raise RuntimeError('Uncovered source occurrence needs original print')
    monkeypatch.setattr(unit_source,'source_unit',needs_repair)
    documents={'ch001.xhtml':'<html><body><img src="'+alias+'"/></body></html>'}
    records={0:{'blocks':[]}}
    frames={0:[dict(text='sample',bbox=[10,20,30,32])]}
    images={alias:dict(page=0,bbox=[0,0,100,100])}
    if not resource_exists:
        with pytest.raises(RuntimeError,match='Uncovered source occurrence'):
            coverage.final_publication(documents,records,frames,images,package,{0:None},{0:'ch001.xhtml'})
    else:
        result=coverage.final_publication(documents,records,frames,images,package,{0:None},{0:'ch001.xhtml'})
        assert result['totals']==dict(inventory=1,live=0,print=1,missing=0,excluded=0)
        assert result['pages'][0]['rows'][0]['image']==alias


@pytest.mark.parametrize('right_edge,needs_repair',[(100,True),(110,False)])
def test_print_coverage_cannot_accept_a_clipped_end_of_the_source_word(monkeypatch,right_edge,needs_repair):
    from cps.services.reflow import occurrence_coverage as coverage,unit_source
    from types import SimpleNamespace
    source=dict(text='waterfront',bbox=[10,20,110,32])
    partial='images/partial.jpg';complete='images/complete.jpg'
    package=SimpleNamespace(images={partial:42},aliases={})
    images={partial:dict(page=0,bbox=[10,20,right_edge,32])}
    documents={'ch001.xhtml':'<html><body><img src="'+partial+'"/></body></html>'}
    calls=[]
    def retain_complete(display,pno,bid,role,writer,assets,reason):
        calls.append(pno);writer.images[complete]=84
        assets[complete]=dict(page=0,bbox=[0,0,120,40])
        return '<p><img src="'+complete+'"/></p>',[],dict(image=complete,source_reference='original')
    monkeypatch.setattr(unit_source,'source_unit',retain_complete)
    result=coverage.final_publication(documents,{0:{'blocks':[]}},{0:[source]},images,package,{0:None},{0:'ch001.xhtml'})
    assert calls==([0]if needs_repair else[])
    assert result['totals']==dict(inventory=1,live=0,print=1,missing=0,excluded=0)
    assert result['pages'][0]['rows'][0]['image']==(complete if needs_repair else partial)
    classified=coverage.classify([source],[],{},{},{partial:images[partial]},0)
    assert classified[0]['disposition']==('missing'if needs_repair else'print')
