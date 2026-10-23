"""Actual C47 Source pixel calls inside alias callbacks retain owned recovery.
Authored after frozen C47 code, before execution; no prepatch injection credit.
"""
import copy,hashlib,importlib.util,inspect,json,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels,vision_reading as vr,build_epub
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_public_note_contract_support import local_ocr_visible
s=importlib.util.spec_from_file_location('alias_ink_fixture',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
@pytest.mark.parametrize('helper',['_complete_note_alias_body_marker','_complete_note_opening_characters'])
def test_actual_alias_source_operation_error_completes_owned_page_with_source_return(tmp_path,monkeypatch,helper):
 before='A healthy emerald neighbor retains every independent original source word.'
 after='A healthy sapphire neighbor preserves every separate original source word.'
 items=[]
 for text in [before,after]:
  p=h.InkPage();p.text(text,'healthy','body',y=55);image,words,answer=p.finish();items.append((image,words,answer,[]))
 p=h.InkPage();prefix='The bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
 p.page.insert_text((24,55),prefix,fontsize=9);p.page.insert_text((x,51),'7',fontsize=5);p.page.insert_text((x+5,55),' carries a finite attribution.',fontsize=9)
 p.blocks.append(h.model_block('The bright ruby<n:fn1> carries a finite attribution.','body','body',[23,41,282,73]))
 p.text('7 A bounded note preserves its original amber attribution.','n1','footnote',y=330,size=7,marker='7',model='A bounded note preserves its original amber attribution.')
 image,words,answer=p.finish();items.insert(1,(image,words,answer,[dict(label='7',bbox=[x,46,x+3,52],kind='bare')]))
 doc=pymupdf.open()
 for image,words,answer,glyphs in items:
  page=doc.new_page(width=h.W,height=h.H);page.insert_image(page.rect,stream=image)
 reader=vr.Reader(None,tmp_path/'cache/readings',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02))
 config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
 for pno in range(3):
  img=SourceDisplay(doc,pno).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90);identity=hashlib.sha256(img).hexdigest()
  (reader.cache/(identity+'-'+config+'.json')).write_text(json.dumps(dict(image_sha256=identity,config_sha256=config,answer=items[pno][2],cost_usd=.001,request_id='synthetic-bounded-alias-'+str(pno))))
 monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(items[display.pno][1]))
 monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:copy.deepcopy(items[display.pno][3]))
 original=SourceDisplay.pixmap;reached=[]
 def fault(display,*args,**kwargs):
  if any(frame.function==helper for frame in inspect.stack()):
   assert display.pno==1;reached.append(display.pno);raise ValueError('synthetic alias original component rendering unavailable')
  return original(display,*args,**kwargs)
 monkeypatch.setattr(SourceDisplay,'pixmap',fault);out=tmp_path/'actual.epub'
 try:report=vp.convert(doc,str(out),reader)
 finally:doc.close()
 (tmp_path/'operation-report.json').write_text(json.dumps(report,indent=2))
 assert reached and set(reached)=={1},'Actual new original pixel operation must be reached inside its named alias helper'
 assert build_epub.validate(out)==[] and set(map(int,report['page_homes']))=={0,1,2}
 assert reader.ledger.spent()==reader.ledger.pending_usd()==0 and report['reread_pages']==[]
 with zipfile.ZipFile(out)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  images={name for name in z.namelist()if name.startswith('OEBPS/images/')}
 assert h.body_texts(docs).count(before)==h.body_texts(docs).count(after)==1,'Healthy original neighbors survive exactly once'
 events=report['unit_recovery_events']
 assert [event.get('transition') for event in events]==[
  'SOURCE_REPAIR_UNPROVED','QUARANTINE_UNTRUSTED_CACHE',
  'REREAD_NOT_DISPATCHED','VISIBLE_SOURCE_DEGRADATION'], 'the actual owned rejection ladder must complete in order'
 assert events[0]['reason']=='synthetic alias original component rendering unavailable'
 assert events[2]['reason']=='offline_or_financial_hold'
 assert events[3]['reason']=='unbound printed note marker'
 receipt=events[1]['receipt'];retained=Path(receipt['path']).read_bytes()
 assert hashlib.sha256(retained).hexdigest()==receipt['sha256']
 rejected=json.loads(retained)
 assert receipt['request_id']=='synthetic-bounded-alias-1' and receipt['cost_usd']==.001
 assert rejected['answer']==items[1][2]
 assert rejected['image_sha256']==receipt['image_sha256'] and rejected['config_sha256']==receipt['config_sha256']
 assert any(event.get('transition')=='SOURCE_REPAIR_UNPROVED'and 'synthetic alias original component rendering unavailable'in event.get('reason','')for event in report['unit_recovery_events'])
 assert h.refs_and_notes(docs)[0]==[]
 local_ocr_visible(out,report,{1:['The bright ruby','carries a finite attribution.',
  'A bounded note preserves its original amber attribution.']},non_local_pages={0,2})
 controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref'];assert controls
 for name,a in controls:
  target_file,target=h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
  assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.NS))
  assert any(h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in nodes[0].findall('.//x:a',h.NS)if '#'in(back.get('href')or ''))
