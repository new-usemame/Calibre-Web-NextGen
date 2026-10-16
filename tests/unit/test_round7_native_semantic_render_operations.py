"""New native projection renderer calls must retain original unit containment.

Authored after frozen C46 code, before first operation-injection execution.
No prepatch-writer failure credit is claimed.
"""
import copy,importlib.util,inspect,zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,vision_pipeline as vp,build_epub
source=Path(__file__).with_name('test_round7_native_sup_note_paragraph_continuation.py')
s=importlib.util.spec_from_file_location('exact_native_sup_fixture',source);f=importlib.util.module_from_spec(s);s.loader.exec_module(f)
@pytest.mark.parametrize('operation,caller,stage',[('_runs_html','published_characters','native_inline_paragraph'),('_nav_runs_html','published_identity','native_seam_binding')])
def test_native_semantic_projection_render_failure_is_owned_and_visible(tmp_path,monkeypatch,operation,caller,stage):
 doc=pymupdf.open();pages={};elements=[];notes=[]
 neighbors=['A healthy emerald neighbor preserves every independent source word.',None,'A healthy sapphire neighbor retains its original source ownership.']
 for pno in range(3):
  folder=tmp_path/str(pno);folder.mkdir();original,book,physical,left,right,last=f.fixture(folder)
  try:
   if neighbors[pno]:
    original[0].insert_text((70,600),neighbors[pno],fontsize=12);b=original[0].search_for(neighbors[pno]);assert len(b)==1
    e=assemble.Element('p',runs=[['t',neighbors[pno]]],pno=0,bbox=tuple(b[0]),line_boxes=[tuple(b[0])]);book.pages[0].append(e)
   doc.insert_pdf(original)
   rows=copy.deepcopy(book.pages[0])
   for e in rows:
    e.pno=pno
    for run in e.runs:
     if run[0]=='sup':run[2]=pno
   ownednotes=copy.deepcopy(book.notes)
   for note in ownednotes:note.pno=pno
   pages[pno]=rows;elements+=rows;notes+=ownednotes
  finally:original.close()
 native=assemble.Book(elements=elements,pages=pages,notes=notes,style=book.style);native.style.page_count=3;before=copy.deepcopy(native);doc.save(tmp_path/'synthetic-three-originals.pdf')
 actual=getattr(build_epub,operation);reached=[]
 def fault(*args,**kwargs):
  frames=[frame for frame in inspect.stack()if frame.function==caller]
  if frames:
   element=frames[0].frame.f_locals.get('e'if caller=='published_characters'else'element')
   if element is not None and element.pno==1:
    reached.append(dict(page=1,caller=caller));raise ValueError('synthetic native semantic projection rendering unavailable')
  return actual(*args,**kwargs)
 monkeypatch.setattr(build_epub,operation,fault)
 class Offline:
  cache=tmp_path/'offline';key=None;calls=0
  def read(self,*a,**k):self.calls+=1;raise AssertionError('No model in native operation control')
  def read_marker(self,*a,**k):self.calls+=1;raise AssertionError('No marker request in native operation control')
 reader=Offline();reader.cache.mkdir();out=tmp_path/'actual.epub'
 try:report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=native))
 finally:doc.close()
 assert reached and all(r['page']==1 for r in reached),'Injection must reach actual new renderer invocation for exact middle Source unit'
 assert reader.calls==0 and native==before and build_epub.validate(out)==[]
 assert set(map(int,report['page_homes']))=={0,1,2},'One renderer fault cannot abandon other original pages'
 (tmp_path/'operation-report.json').write_text(__import__('json').dumps(report,indent=2))
 with zipfile.ZipFile(out)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  images={name for name in z.namelist()if name.startswith('OEBPS/images/')}
 live=' '.join(f.plain(n)for root in docs.values()for n in root.iter()if f.tag(n)=='p')
 assert live.count(neighbors[0])==live.count(neighbors[2])==1,'Healthy original neighbor prose survives once'
 cases=report['degraded_units'];assert cases and all(case['page']==1 for case in cases),'Source failure remains owned by the affected physical page'
 assert any(event.get('stage')==stage and event.get('error_type')=='ValueError'for event in report['unit_recovery_events']),'Original guarded stage/type must remain visible in report'
 assert any('synthetic native semantic projection rendering unavailable'in case.get('reason','')for case in cases)
 controls=[(name,a)for name,root in docs.items()for a in root.iter()if f.tag(a)=='a'and a.get('class')=='source-disclosure-ref'];assert controls
 for home,control in controls:
  targetfile,tid=control.get('href').split('#');targetfile='OEBPS/'+targetfile if not targetfile.startswith('OEBPS/')else targetfile
  targetfile=targetfile if control.get('href').split('#')[0]else home
  assert targetfile in docs
  targets=[n for n in docs[targetfile].iter()if n.get('id')==tid];assert len(targets)==1
  assert any('OEBPS/'+img.get('src')in images for img in targets[0].iter()if f.tag(img)=='img')
  assert any(f.tag(a)=='a'and a.get('href','').split('#')[-1]==control.get('id')for a in targets[0].iter()),'Source evidence Return must reach exact published control'
