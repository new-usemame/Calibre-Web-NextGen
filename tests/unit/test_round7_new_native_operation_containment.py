"""Exact new native/TABLE operation failures inside public SOURCE unit guards.
Text and geometry are written for these tests; no provider or model calls.
"""
import copy,importlib.util,inspect,zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from PIL import Image
from cps.services.reflow import assemble,skeleton,build_epub,vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

NATIVE={'publication_source':'native_publication_source','vector_width':'native_vector_ownership',
 'row_words':'native_row_ownership','vector_context':'native_vector_context',
 'inline_source':'native_inline_source','inline_words':'native_inline_paragraph'}

def context(frame,name):
 while frame:
  if frame.f_code.co_name==name:return frame
  frame=frame.f_back
 return None

def verify(out,report,reached,stage,pages,healthy):
 assert len(reached)==1,'The exact dependency operation/predicate must fail once inside its actual public guard'
 assert build_epub.validate(out)==[] and set(map(int,report['page_homes']))==set(pages)
 units=report['degraded_units'];assert units and all(u['page']==1 for u in units)
 assert any(u['stage']==stage and u['reason'] for u in units)
 assert any(e.get('stage')==stage for e in report['unit_recovery_events'])
 assert report['reread_pages']==[],'Offline native fixture has no authorized targeted route'
 source_visible(out,report,pages)
 with zipfile.ZipFile(out)as z:
  docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
  bodies=h.body_texts(docs)
  for text in healthy:assert bodies.count(text)==1,'Unrelated healthy native prose must survive exactly once'
  refs=[(home,a)for home,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref']
  for u in units:
   own=[(home,a)for home,a in refs if a.get('href','').split('#')[-1]==u['source_reference']];assert own
   for home,a in own:
    dest,ident=h.destination(home,a.get('href'));target=next(n for n in docs[dest].iter()if n.get('id')==ident)
    assert target.findall('.//x:img',h.NS)
    assert any(back.text=='Return' and h.destination(dest,back.get('href'))==(home,a.get('id'))for back in target.findall('.//x:a',h.NS))

@pytest.mark.parametrize('fault',list(NATIVE))
def test_native_source_owned_operation_rejection_completes_neighbors(tmp_path,monkeypatch,fault):
 doc=pymupdf.open();pages={}
 for pno,text in enumerate(TEXTS):
  page=doc.new_page(width=440,height=600);page.insert_text((40,60),text,fontsize=8)
  ws=page.get_text('words');box=(min(w[0]for w in ws),min(w[1]for w in ws),max(w[2]for w in ws),max(w[3]for w in ws))
  pages[pno]=[assemble.Element('p',runs=[['t',text]],pno=pno,bbox=box,line_boxes=[box])]
  if pno==1:
   for text,y in [('The luminous quartz account continues with',100),('its exact unindented original explanation.',114)]:
    page.insert_text((40,y),text,fontsize=8)
    span=next(s for b in page.get_text('dict')['blocks']for line in b.get('lines',[])for s in line['spans']if s['text']==text)
    box=tuple(span['bbox']);pages[pno].append(assemble.Element('p',runs=[['t',text]],pno=pno,bbox=box,line_boxes=[box]))
   if fault in ('vector_width','vector_context'):
    art=(60,180,360,320);page.draw_rect(art,color=(0,0,0),width=1)
    for x in (100,170,240,310):page.draw_line((x,180),(x,320),color=(0,0,0),width=1)
    for y in (210,250,290):page.draw_line((60,y),(360,y),color=(0,0,0),width=1)
    if fault=='vector_context':
     text='Independent readable source remains inside the unproved vector territory.'
     page.insert_text((70,250),text,fontsize=7)
     span=next(s for b in page.get_text('dict')['blocks']for line in b.get('lines',[])for s in line['spans']if s['text']==text)
     box=tuple(span['bbox']);pages[pno].append(assemble.Element('p',runs=[['t',text]],pno=pno,bbox=box,line_boxes=[box]))
 book=assemble.Book(elements=[e for es in pages.values()for e in es],pages=pages,style=skeleton.BookStyle(body_size=8,page_count=3));before=copy.deepcopy(book);reached=[]
 if fault=='publication_source':
  actual=pymupdf.Page.get_drawings
  def operation(page,*a,**kw):
   frame=inspect.currentframe().f_back
   if page.number==1 and frame.f_code.co_name=='_native_publication_ownership' and not reached:
    if fault=='publication_source':reached.append('get_drawings');raise ValueError('synthetic original drawing operation rejection')
    result=copy.deepcopy(actual(page,*a,**kw));assert result;result[0]['width']='invalid synthetic source width';reached.append('drawing width original float predicate');return result
   return actual(page,*a,**kw)
  monkeypatch.setattr(pymupdf.Page,'get_drawings',operation)
 elif fault=='vector_width':
  actual=pymupdf.Page.cluster_drawings
  def operation(page,*a,**kw):
   result=actual(page,*a,**kw);caller=inspect.currentframe().f_back
   if page.number==1 and caller.f_code.co_name=='_native_publication_ownership' and not reached:
    assert result and kw.get('drawings');kw['drawings'][0]['width']='invalid synthetic source width'
    reached.append('returned drawing width after original source clusters')
   return result
  monkeypatch.setattr(pymupdf.Page,'cluster_drawings',operation)
 elif fault=='inline_source':
  actual=pymupdf.Page.get_text
  def operation(page,*a,**kw):
   caller=inspect.currentframe().f_back
   if page.number==1 and caller.f_code.co_name=='_native_inline_paragraph_ownership' and a and a[0]=='dict' and not reached:
    assert kw.get('flags')==pymupdf.TEXTFLAGS_TEXT;reached.append('get_text(dict,TEXTFLAGS_TEXT)');raise ValueError('synthetic original inline source dictionary rejection')
   return actual(page,*a,**kw)
  monkeypatch.setattr(pymupdf.Page,'get_text',operation)
 elif fault in ('row_words','inline_words'):
  actual=vp.owned_words
  def operation(words,box,*a,**kw):
   caller=inspect.currentframe().f_back;owner=context(caller,'_native_publication_ownership'if fault=='row_words'else'_native_inline_paragraph_ownership')
   if owner and owner.f_locals.get('pno')==1 and not reached and ((fault=='row_words'and caller.f_code.co_name=='_native_publication_ownership')or(fault=='inline_words'and caller.f_code.co_name=='source_rows')):
    reached.append('owned_words exact '+fault);raise ValueError('synthetic native original row ownership operation rejection')
   return actual(words,box,*a,**kw)
  monkeypatch.setattr(vp,'owned_words',operation)
 else:
  from cps.services.reflow import unit_source
  actual=unit_source.source_unit
  def operation(display,pno,bid,role,*a,**kw):
   result=actual(display,pno,bid,role,*a,**kw)
   if pno==1 and bid.startswith('native-vector-')and not reached:
    reached.append('actual source_unit returned missing vector control');return ('',result[1],result[2])
   return result
  monkeypatch.setattr(unit_source,'source_unit',operation)
 class OfflineReader:
  cache=tmp_path/'offline'
  def read(self,*a,**kw):raise AssertionError('Synthetic native operation tests have no primary dispatch')
 OfflineReader.cache.mkdir();out=tmp_path/'actual.epub'
 try:report=vp.convert(doc,str(out),OfflineReader(),native_prepared=SimpleNamespace(book=book))
 finally:doc.close()
 assert book==before,'Native preparation stays immutable across failed disposable attempts'
 verify(out,report,reached,NATIVE[fault],{0,1,2},[TEXTS[0],TEXTS[2]])

@pytest.mark.parametrize('fault',['pixmap','png_bytes','PIL_open','PIL_mask_bytes'])
def test_source_table_new_dependency_failure_is_visible_without_losing_live_body(tmp_path,monkeypatch,fault):
 path=Path(__file__).with_name('test_cached_table_live_source_cells_v2.py')
 spec=importlib.util.spec_from_file_location('r7_original_table_guard_fixture',path);table=importlib.util.module_from_spec(spec);spec.loader.exec_module(table)
 reached=[]
 if fault=='pixmap':owner,attr=SourceDisplay,'pixmap'
 elif fault=='png_bytes':owner,attr=pymupdf.Pixmap,'tobytes'
 elif fault=='PIL_open':owner,attr=Image,'open'
 else:owner,attr=Image.Image,'tobytes'
 actual=getattr(owner,attr)
 def operation(*a,**kw):
  caller=inspect.currentframe().f_back
  if caller.f_code.co_name=='_source_grid_live_text':
   reached.append(fault);raise ValueError('synthetic source TABLE '+fault+' operation rejection')
  return actual(*a,**kw)
 monkeypatch.setattr(owner,attr,operation);r=table.publish(tmp_path,monkeypatch,'flat')
 assert 1<=len(reached)<=2 and all(x==fault for x in reached) and r['visible'].count(table.BODY)==1
 units=r['report']['degraded_units'];assert units and all(u['page']==0 and u.get('block')=='t1'for u in units)
 assert any(u['stage']=='page_compilation' and fault in u['reason']for u in units)
 assert any(e.get('stage')=='page_compilation'for e in r['report']['unit_recovery_events'])
 source_visible(tmp_path/'actual.epub',r['report'],{0})
 nodes={n.get('id'):n for root in r['roots']for n in root.iter()if n.get('id')}
 controls=[n for root in r['roots']for n in root.iter()if table.tag(n)=='a'and 'source-disclosure-ref'in n.get('class','')]
 for u in units:
  owned=[a for a in controls if a.get('href','').split('#')[-1]==u['source_reference']];assert owned
  for a in owned:
   target=nodes[a.get('href').split('#')[-1]];assert any(table.tag(n)=='img'for n in target.iter())
   assert any(table.tag(n)=='a'and n.text=='Return'and n.get('href','').split('#')[-1]==a.get('id')for n in target.iter())
