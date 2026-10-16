"""Actual original-component pixel operation must stay within its owned page."""
import copy,hashlib,importlib.util,inspect,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay
s=importlib.util.spec_from_file_location('frozen_complete_slot_fixture',Path(__file__).with_name('test_round7_complete_original_marker_slot.py'));f=importlib.util.module_from_spec(s);s.loader.exec_module(f);h=f.h

def test_component_source_pixmap_failure_lists_only_owned_page_and_keeps_neighbors(tmp_path,monkeypatch):
 before='A healthy emerald paragraph retains every ordinary source word.'
 after='A healthy sapphire paragraph preserves its independent source owner.'
 items=[]
 for text in [before,after]:
  p=h.h.InkPage();p.text(text,'healthy','body',y=55);image,words,answer=p.finish();items.append((image,words,answer,[]))
 prepared,whole=f.damaged('13','3',True);items.insert(1,prepared)
 doc=pymupdf.open()
 for image,words,answer,glyphs in items:
  page=doc.new_page(width=h.h.W,height=h.h.H);page.insert_image(page.rect,stream=image)
 monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(items[display.pno][1]))
 monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:copy.deepcopy(items[display.pno][3]))
 original=SourceDisplay.pixmap;reached=[]
 def fault(display,*args,**kwargs):
  if any(frame.function=='_original_marker_slot_components'for frame in inspect.stack()):
   assert display.pno==1,'Original-component operation cannot be attributed to another physical page'
   reached.append(display.pno);raise ValueError('synthetic original marker component pixel operation unavailable')
  return original(display,*args,**kwargs)
 monkeypatch.setattr(SourceDisplay,'pixmap',fault)
 class OfflineReader:
  cache=tmp_path/'cache/readings';key=None
  def __init__(self):self.images={};self.marker_calls=0
  def read(self,image):
   digest=hashlib.sha256(image).hexdigest()
   if digest not in self.images:self.images[digest]=len(self.images)
   return dict(answer=copy.deepcopy(items[self.images[digest]][2]),cached=True,new_cost_usd=0)
  def read_marker(self,image):self.marker_calls+=1;raise AssertionError('Failed original component operation must not dispatch marker reading')
 reader=OfflineReader();path=tmp_path/'contained-components.epub'
 try:report=vp.convert(doc,str(path),reader)
 finally:doc.close()
 assert reached and set(reached)=={1},'Fault must reach actual SourceDisplay.pixmap within original-component helper'
 assert reader.marker_calls==0 and build_epub.validate(path)==[]
 assert set(map(int,report['page_homes']))=={0,1,2},'All physical pages complete despite owned component operation failure'
 with zipfile.ZipFile(path)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  images={name for name in z.namelist()if name.startswith('OEBPS/images/')}
 live=h.h.body_texts(docs);assert live.count(before)==live.count(after)==1,'Healthy source neighbors remain once'
 cases=report['degraded_units'];assert cases and all(case['page']==1 for case in cases)
 assert any(event.get('stage')=='page_compilation'and event.get('error_type')=='ValueError'for event in report['unit_recovery_events'])
 assert any('synthetic original marker component pixel operation unavailable'in case.get('reason','')for case in cases)
 controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.h.NS)if a.get('class')=='source-disclosure-ref'];assert controls
 for name,a in controls:
  target_file,target=h.h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
  assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.h.NS))
  assert any(h.h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in nodes[0].findall('.//x:a',h.h.NS)if '#'in(back.get('href')or '')),'Source Return must reach exact original owned control'
