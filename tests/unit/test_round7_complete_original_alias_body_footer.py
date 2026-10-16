"""Additive complete original alias evidence; synthetic pixels, frozen prepatch."""
import copy,importlib.util,json,re
from pathlib import Path
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels
from cps.services.reflow.source_display import SourceDisplay
_spec=importlib.util.spec_from_file_location('synthetic_alias_original_fixture',Path(__file__).with_name('test_round4_defects.py'));f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)
@pytest.mark.parametrize('body_label,footer_label,response_label,expected',[('13','13','13',True),('3','13','13',False),('13','3','13',False),('*','*','*',True)])
def test_alias_requires_complete_actual_body_and_footer_original_label(body_label,footer_label,response_label,expected,tmp_path,monkeypatch):
    ink=f.InkPage();prefix='The bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
    ink.page.insert_text((24,55),prefix,fontsize=9);ink.page.insert_text((x,51),body_label,fontsize=5)
    width=pymupdf.get_text_length(body_label,fontsize=5)
    ink.page.insert_text((x+width+1,55),' carries a finite attribution.',fontsize=9)
    ink.page.insert_text((24,69),'Every later synthetic source word remains intact.',fontsize=9)
    ink.blocks.append(f.model_block('The bright ruby<n:fn1> carries a finite attribution. Every later synthetic source word remains intact.','body','body',[23,41,282,73]))
    ink.text(footer_label+' A bounded note preserves its original amber attribution.','n1','footnote',y=330,size=7,marker=response_label,model='A bounded note preserves its original amber attribution.')
    image,optical,answer=ink.finish()
    body=[w for w in optical if w['text']==body_label and w['bbox'][1]<80];footer=[w for w in optical if w['text']==footer_label and w['bbox'][1]>300];ruby=[w for w in optical if w['text']=='ruby']
    assert len(body)==len(footer)==len(ruby)==1,'Fixture Source body/footer printed labels have unique actual original words'
    original_body_bbox=list(body[0]['bbox']);original_footer_bbox=list(footer[0]['bbox'])
    ruby[0]['text']='ruby'+response_label;ruby[0]['bbox']=vp.vision_reading.union([ruby[0]['bbox'],body[0]['bbox']]);optical.remove(body[0])
    footer[0]['text']=response_label;footer[0]['confidence']=80
    original_answer=copy.deepcopy(answer);optical_before=copy.deepcopy(optical)
    doc=pymupdf.open();page=doc.new_page(width=f.W,height=f.H);page.insert_image(page.rect,stream=image);display=SourceDisplay(doc,0);source=tmp_path/'synthetic-original.pdf';doc.save(source)
    calls=[]
    def raised(display,box,cache):
        q=vision_pixels.ink_bounds(display,original_body_bbox);assert q is not None
        calls.append(dict(label=response_label,bbox=list(q)))
        return [dict(label=response_label,bbox=list(q),kind='bare')]
    monkeypatch.setattr(vision_pixels,'raised_markers',raised)
    try:
        result=vp._source_note_id_repair(answer,display,copy.deepcopy(optical),tmp_path/'optical-cache')
        assert answer==original_answer and optical==optical_before
        (tmp_path/'probe-observation.json').write_text(json.dumps(dict(body_label=body_label,footer_label=footer_label,response_label=response_label,expected_repair=expected,repair_returned=result is not None,candidate_calls=len(calls),actual_body_bbox=original_body_bbox,actual_footer_bbox=original_footer_bbox,result=result),indent=2))
        assert calls,'The actual Source callback must reach independently printed body glyph, not decline before the contract'
        if expected:
            assert result is not None,'Complete source-matched body/footer label must retain deterministic alias repair'
            fixed,receipt=result;assert fixed['blocks'][0]['text']==answer['blocks'][0]['text'].replace('<n:fn1>','<n:n1>')
            assert fixed['blocks'][1]['marker']==response_label and receipt['mapping']=={'fn1':'n1'}
            assert len(receipt['source_evidence']['references'])==len(receipt['source_evidence']['notes'])==1
            assert receipt['source_evidence']['references'][0]['source_proved']and receipt['source_evidence']['notes'][0]['source_proved']
        else:
            assert result is None,'Mutually agreeing optical and candidate label cannot override actual different original body/footer cardinality'
    finally:doc.close()
