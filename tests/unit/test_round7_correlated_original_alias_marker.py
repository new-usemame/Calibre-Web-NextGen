"""Hypothesis-only pure Source callback probe; all printed text is synthetic."""
import copy,importlib.util,json
from pathlib import Path
import pymupdf
from cps.services.reflow import vision_pipeline as vp,vision_pixels
from cps.services.reflow.source_display import SourceDisplay
_spec=importlib.util.spec_from_file_location('frozen_synthetic_ink',Path(__file__).with_name('test_round4_defects.py'))
f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)

def test_correlated_partial_body_and_footer_optical_cannot_prove_alias(tmp_path,monkeypatch):
    ink=f.InkPage();prefix='The bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
    ink.page.insert_text((24,55),prefix,fontsize=9)
    ink.page.insert_text((x,51),'13',fontsize=5)
    ink.page.insert_text((x+6,55),' carries a finite attribution.',fontsize=9)
    ink.page.insert_text((24,69),'Every later synthetic source word remains intact.',fontsize=9)
    ink.blocks.append(f.model_block('The bright ruby<n:fn1> carries a finite attribution. Every later synthetic source word remains intact.','body','body',[23,41,282,73]))
    ink.text('13 A bounded note preserves its original amber attribution.','n1','footnote',y=330,size=7,marker='3',model='A bounded note preserves its original amber attribution.')
    # The raw original character is selected only by its synthetic printed row.
    chars=[c for b in ink.page.get_text('rawdict')['blocks']for line in b.get('lines',[])for span in line['spans']for c in span['chars']]
    body_third=[c for c in chars if c['c']=='3'and c['bbox'][1]<80];assert len(body_third)==1
    partial_bbox=list(body_third[0]['bbox'])
    image,optical,answer=ink.finish()
    body_labels=[w for w in optical if w['text']=='13'and w['bbox'][1]<80];footer_labels=[w for w in optical if w['text']=='13'and w['bbox'][1]>300]
    assert len(body_labels)==len(footer_labels)==1,'Both original printed labels are full13, not optical inference'
    ruby=[w for w in optical if w['text']=='ruby'];assert len(ruby)==1
    marker=body_labels[0];ruby[0]['text']='ruby3';ruby[0]['bbox']=vp.vision_reading.union([ruby[0]['bbox'],marker['bbox']]);optical.remove(marker)
    footer_labels[0]['text']='3';footer_labels[0]['confidence']=80
    assert not any(w['text']=='3'and w['bbox'][1]<80 for w in optical),'No separate body optical label'
    original=copy.deepcopy(answer);optical_before=copy.deepcopy(optical)
    doc=pymupdf.open();page=doc.new_page(width=f.W,height=f.H);page.insert_image(page.rect,stream=image);display=SourceDisplay(doc,0)
    source=tmp_path/'synthetic-original.pdf';doc.save(source)
    calls=[]
    def raised(display,box,cache):
        q=vision_pixels.ink_bounds(display,partial_bbox);assert q is not None
        calls.append(dict(kind='supplied_partial_raised_candidate',bbox=list(q)))
        return [dict(label='3',bbox=list(q),kind='bare')]
    monkeypatch.setattr(vision_pixels,'raised_markers',raised)
    try:
        result=vp._source_note_id_repair(answer,display,copy.deepcopy(optical),tmp_path/'optical-cache')
        assert answer==original and optical==optical_before,'Callback must not mutate input response or optical witness'
        (tmp_path/'probe-observation.json').write_text(json.dumps(dict(source_path=str(source),candidate_calls=len(calls),deterministic_repair_returned=result is not None,body_original_label='13',footer_original_label='13',response_marker='3',footer_optical_confidence=80,source_character_bbox=partial_bbox,result=result),indent=2))
        assert calls,'The actual callback must reach supplied original raised candidate, not decline before the hypothesized path'
        assert result is None,'Correlated partial body/footer optical3 cannot prove actual Original13 alias repair'
    finally:doc.close()
