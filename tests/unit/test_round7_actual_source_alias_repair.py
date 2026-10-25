"""Wire alias repair is accepted only through converter original-pixel proof."""
import copy,hashlib,importlib.util,json,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels,vision_reading as vr,build_epub
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_public_note_contract_support import local_ocr_visible
_spec=importlib.util.spec_from_file_location('r7_actual_alias_ink',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
BODY='The bright ruby carries a finite attribution. Every later source word remains intact.'
NOTE='A bounded note preserves its original amber attribution.'


@pytest.mark.parametrize('defect',['none','wrong_body_symbol','missing_body_symbol','wrong_note_symbol'])
def test_actual_source_alias_callback_requires_printed_reference_and_opening(defect,tmp_path,monkeypatch):
    page=h.InkPage();prefix='The bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
    page.page.insert_text((24,55),prefix,fontsize=9)
    physical='9' if defect=='wrong_body_symbol' else '7'
    if defect!='missing_body_symbol':page.page.insert_text((x,51),physical,fontsize=5)
    page.page.insert_text((x+5,55),' carries a finite attribution.',fontsize=9)
    page.page.insert_text((24,69),'Every later source word remains intact.',fontsize=9)
    page.blocks.append(h.model_block('The bright ruby<n:fn1> carries a finite attribution. Every later source word remains intact.','body','body',[23,41,282,73]))
    page.text(('9' if defect=='wrong_note_symbol' else '7')+' '+NOTE,'n1','footnote',y=330,size=7,marker='7',model=NOTE)
    image,optical,answer=page.finish();doc=pymupdf.open();source=doc.new_page(width=h.W,height=h.H);source.insert_image(source.rect,stream=image)
    display=SourceDisplay(doc,0);reading_image=display.pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90)
    reader=vr.Reader(None,tmp_path/'cache'/'readings',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02))
    identity=hashlib.sha256(reading_image).hexdigest();config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    cache=reader.cache/(identity+'-'+config+'.json');cache.write_text(json.dumps(dict(image_sha256=identity,config_sha256=config,answer=answer,cost_usd=.001,request_id='synthetic-complete-rejected-alias')))
    rejected=cache.read_bytes();calls=[]
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(optical))
    def characters(display,box,cache):
        calls.append(('locked-original-character',list(box)))
        return [] if defect=='missing_body_symbol' else [dict(label=physical,bbox=[x,46,x+3,52],kind='bare')]
    monkeypatch.setattr(vision_pixels,'raised_markers',characters)
    path=tmp_path/'actual-source-alias.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert build_epub.validate(path)==[] and set(map(int,report['page_homes']))=={0}
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0 and report['reread_pages']==[]
    with zipfile.ZipFile(path) as archive:
        docs={name:ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml') and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
        if defect=='none':
            assert report['degraded_units']==[]
            refs,notes=h.assert_exact_returns(docs,1)
            assert refs[0][1].get('href').endswith('#v4-note-0-n1')
            assert sum(NOTE in ''.join(note.itertext()) for _,note in notes)==1
            assert h.body_texts(docs)==[BODY], 'actual source-proved wire repair preserves every lexical BODY word once'
            repaired=json.loads(cache.read_text());assert repaired['answer']['blocks'][0]['text']==answer['blocks'][0]['text'].replace('<n:fn1>','<n:n1>')
            retained=repaired['rejected_receipt'];assert Path(retained['path']).read_bytes()==rejected
        else:
            assert h.refs_and_notes(docs)[0]==[]
            assert any(p.read_bytes()==rejected for p in reader.cache.rglob('*') if p.is_file()), 'rejected source-marker evidence remains exact'
            local_ocr_visible(path,report,{0:['The bright ruby','carries a finite attribution.',
                'Every later source word remains intact.',NOTE]})
