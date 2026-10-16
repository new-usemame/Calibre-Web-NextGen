"""Additive synthetic actions on actual list split/join; authored after C42 freeze."""
import copy,importlib.util,json,zipfile,re
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,extract,vision_pipeline as vp
_spec=importlib.util.spec_from_file_location('frozen_list_fixture',Path(__file__).with_name('test_round7_native_list_source_continuation.py'))
f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)
@pytest.mark.parametrize('action',['outgoing_suffix','incoming_next_item','printed_note_origin'])
def test_actual_native_list_repair_conserves_original_actions_and_return_owners(tmp_path,monkeypatch,action):
 actual=vp.convert;observed={}
 def publish(original,out,reader,*,native_prepared,**kwargs):
  # Fixture-only exact copy retains caller Book and independently printed PDF.
  book=copy.deepcopy(native_prepared.book);work=pymupdf.open();work.insert_pdf(original)
  if action=='printed_note_origin':
   first=book.pages[0][-1];item=first.list_items[0];b=first.list_item_boxes[0][0]
   x=b[2]+1;work[0].insert_text((x,704),'1',fontsize=7)
   first.list_items[0]=copy.deepcopy(item)+[['sup','1',0]]
   first.runs=copy.deepcopy(first.list_items[0])+[['t',' ']]+copy.deepcopy(first.list_items[1])
   mark=work[0].search_for('1')[-1];first.list_item_boxes[0]=[(b[0],min(b[1],mark.y0),max(b[2],mark.x1),b[3])]
   work[0].insert_text((85,782),'1 The independently printed note has its own Return.',fontsize=10)
   note=work[0].search_for('1 The independently printed note has its own Return.')[0]
   book.notes=[assemble.Note(1,'The independently printed note has its own Return.',0,bbox=tuple(note))]
  else:
   source_page,source_label,dest_page,dest_label=(1,'remaining',0,'initial')if action=='outgoing_suffix'else(0,'initial',1,'following')
   boxes=work[source_page].search_for(source_label);targets=work[dest_page].search_for(dest_label);assert len(boxes)==len(targets)==1
   target=targets[0];work[source_page].insert_link({'kind':pymupdf.LINK_GOTO,'from':boxes[0],'page':dest_page,'to':pymupdf.Point(target.x0,(target.y0+target.y1)/2)})
  source=tmp_path/'action-original.pdf';work.save(source);work.close();work=pymupdf.open(source)
  raw=[extract.read_page(work,p,keep_char_boxes=True)for p in range(2)]
  assemble._bind_source_navigation(book,raw)
  if action!='printed_note_origin':assert len(book.source_navigation)==1 and book.source_navigation[0]['status']=='resolved','The synthetic original action must bind before publication'
  before=copy.deepcopy(book);observed['book']=before
  try:
   result=actual(work,out,reader,native_prepared=SimpleNamespace(book=book,raw_pages=raw),**kwargs)
   (tmp_path/'modified-action-report.json').write_text(json.dumps(result,indent=2))
   assert book==before,'Caller prepared semantic list and action owners must remain immutable'
   return result
  finally:work.close()
 monkeypatch.setattr(vp,'convert',publish)
 r=f.publish(tmp_path,'plain');joined=r['left']+' '+r['suffix']
 assert sum(f.plain(n)==joined for n in r['lists'])==1,'Action-built continuation must remain one source-proved last LI'
 assert sum(f.plain(n)==f.NEXT for n in r['lists'])==1,'Next original numbered item must remain separate'
 with zipfile.ZipFile(tmp_path/'actual.epub')as z:docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
 actual_lists=[n for root in docs.values()for n in root.iter()if f.tag(n)=='li']
 ids={}
 for home,root in docs.items():
  for n in root.iter():
   if n.get('id'):ids.setdefault(n.get('id'),[]).append((home,n))
 if action!='printed_note_origin':
  prior=observed['book'].source_navigation[0];rid=prior['id'];origin='pdfref_'+rid.removeprefix('pdfgoto_')
  assert len(ids.get(rid,[]))==len(ids.get(origin,[]))==1
  links=[(home,n)for home,root in docs.items()for n in root.iter()if f.tag(n)=='a'and n.get('href','').split('#')[-1]==rid]
  assert len(links)==1 and f.plain(links[0][1])==prior['label'],'Source action label and original destination remain exact once'
  expected=joined if action=='outgoing_suffix'else f.FIRST
  assert sum(f.plain(n)==expected and any(c is links[0][1]for c in n.iter())for n in actual_lists)==1,'Rebound annotation must remain inside its exact original numbered item'
  target_owner=f.NEXT if action=='incoming_next_item'else f.FIRST
  assert sum(f.plain(n)==target_owner and any(c is ids[rid][0][1]for c in n.iter())for n in actual_lists)==1,'Destination anchor stays inside its own original item after splitting'
  backs=[n for root in docs.values()for n in root.iter()if f.tag(n)=='a'and n.get('href','').split('#')[-1]==origin]
  assert len(backs)==1 and 'Return'in f.plain(backs[0]),'Exact original Return-to-origin action survives once'
  assert r['report']['degraded_unit_count']==0,'Healthy original navigation cannot degrade either list page'
 else:
  refs=[(home,n)for home,root in docs.items()for n in root.iter()if f.tag(n)=='a'and 'noteref'in n.get('class','').split()]
  assert len(refs)==1 and f.plain(refs[0][1])=='1'
  home,ref=refs[0];target=ref.get('href','').split('#')[-1];origin=ref.get('id');assert len(ids.get(target,[]))==1
  assert sum(f.FIRST in f.plain(n)and any(c is ref for c in n.iter())for n in actual_lists)==1,'Printed note origin remains inside its own first list item'
  desthome,dest=ids[target][0];assert 'independently printed note' in f.plain(dest)
  backs=[n for n in docs[desthome].iter()if f.tag(n)=='a'and n.get('href','').split('#')[-1]==origin]
  assert len(backs)==1,'Printed note renders exactly one back-link to its own surviving origin'
  back=backs[0]
  assert back.get('class')=='note-return'
  assert back.get('aria-label')=='Return to note reference'
  assert ''.join(back.itertext())=='1','Back-link shows exactly the printed note number, without invented words'
  assert sum(f.tag(n)=='a' and n.get('class')=='note-return' for root in docs.values() for n in root.iter())==1
