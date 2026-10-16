"""Synthetic original numeric ink must prove marker ROLE before identity or literal reuse."""
import copy,hashlib,importlib.util,inspect
from pathlib import Path
import pymupdf,pytest
p=Path(__file__).with_name('test_source_placeholder_display_marker_v2.py')
s=importlib.util.spec_from_file_location('readonly_numeric_marker_fixture',p);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay

def numeric_ink(raised=False):
 p=h.h.InkPage();font=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
 prefix='A bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
 p.page.insert_text((24,55),prefix,fontsize=9)
 size=5 if raised else 9;baseline=51 if raised else 55;width=font.text_length('3',fontsize=size)
 p.page.insert_text((x,baseline),'3',fontsize=size,fontname='unicode')
 glyph=h.source_ink(p.page,[x-.1,baseline-size*1.5,x+width+.4,baseline+1])
 lexical=h.source_ink(p.page,[24,40,x-.2,60])
 p.page.insert_text((x+width+3,55),' carries clear light.',fontsize=9)
 p.page.insert_text((24,69),'Every healthy word remains once.',fontsize=9)
 p.blocks.append(h.h.model_block(prefix+'3<n:n1> carries clear light. Every healthy word remains once.','body','body',[23,38,282,74]))
 p.page.insert_text((24,330),'3 '+h.NOTE,fontsize=7,fontname='unicode')
 p.blocks.append(h.h.model_block(h.NOTE,'n1','footnote',[23,318,282,335],marker='3'))
 def dark_count(box):
  pix=p.page.get_pixmap(matrix=pymupdf.Matrix(12,12),clip=pymupdf.Rect(box),colorspace=pymupdf.csGRAY,alpha=False)
  return sum(value<180 for value in pix.samples)
 glyph_count=dark_count(glyph);lexical_count=dark_count(lexical)
 assert glyph_count>0 and lexical_count>0
 image,words,answer=p.finish()
 geometry={'source_rgb_glyph_ink_bbox':glyph,'source_rgb_lexical_ink_bbox':lexical,'actual_printed_font_size':size,'actual_printed_baseline':baseline,'lexical_font_size':9,'lexical_baseline':55,'source_rgb_glyph_dark_pixels_12x':glyph_count,'source_rgb_lexical_dark_pixels_12x':lexical_count}
 return (image,words,answer,[dict(label='3',bbox=glyph,kind='bare')]),geometry

def exact_note_owner(docs):
 refs,notes=h.h.assert_exact_returns(docs,1);assert len(refs)==1
 assert refs[0][1].get('href').endswith('#v4-note-0-n1')
 assert h.NOTE in ' '.join(''.join(n.itertext())for _,n in notes)
 return refs

def working_disclosed_original(report,docs,images):
 controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.h.NS)if a.get('class')=='source-disclosure-ref']
 assert controls,'Unknown numeric marker role needs visible original SOURCE evidence'
 for name,a in controls:
  target_file,target=h.h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
  assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.h.NS))
  assert any(h.h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in nodes[0].findall('.//x:a',h.h.NS)if '#'in(back.get('href')or ''))
 assert report['note_marker_disclosures']

def test_matching_footer_and_no_punctuation_do_not_promote_baseline_digit(tmp_path,monkeypatch):
 prepared,geometry=numeric_ink(False)
 assert geometry['actual_printed_font_size']==geometry['lexical_font_size']and geometry['actual_printed_baseline']==geometry['lexical_baseline']
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch,marker_response={'label':'3','uncertain':False,'request_dispatched':False})
 exact_note_owner(docs)
 assert h.h.body_texts(docs)==['A bright ruby3 carries clear light. Every healthy word remains once.'],'Even a matching known footer cannot remove ordinary printed baseline3 through literal/placeholder reconciliation'
 assert not any(r.get('status')=='PROVED'for r in report['note_marker_repairs']),'Character identity alone cannot certify note-marker ROLE'
 working_disclosed_original(report,docs,images)

def test_genuine_smaller_higher_numeric_literal_placeholder_remains_one_action(tmp_path,monkeypatch):
 prepared,geometry=numeric_ink(True)
 assert geometry['source_rgb_glyph_ink_bbox'][1]<geometry['source_rgb_lexical_ink_bbox'][1]
 assert geometry['source_rgb_glyph_ink_bbox'][3]-geometry['source_rgb_glyph_ink_bbox'][1]<geometry['source_rgb_lexical_ink_bbox'][3]-geometry['source_rgb_lexical_ink_bbox'][1]
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch)
 refs=exact_note_owner(docs);assert ''.join(refs[0][1].itertext())=='3'
 assert h.h.body_texts(docs)==[h.BODY]
 assert report['note_marker_disclosures']==[]

def test_owned_numeric_role_source_operation_failure_preserves_healthy_neighbor(tmp_path,monkeypatch):
 prepared,_=numeric_ink(True);p=h.h.InkPage();healthy='A distant healthy paragraph remains exactly once.';p.text(healthy,'healthy');second=p.finish();items=[prepared,(*second,[])]
 doc=pymupdf.open()
 for image,words,answer,glyphs in items:
  page=doc.new_page(width=h.h.W,height=h.h.H);page.insert_image(page.rect,stream=image)
 monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(items[display.pno][1]))
 monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:copy.deepcopy(items[display.pno][3]))
 original=SourceDisplay.pixmap;calls=[]
 def fault(display,*args,**kwargs):
  if display.pno==0 and any(frame.function=='_numeric_original_marker_role'for frame in inspect.stack()):
   calls.append(display.pno);raise ValueError('synthetic numeric role original pixel operation failure')
  return original(display,*args,**kwargs)
 monkeypatch.setattr(SourceDisplay,'pixmap',fault)
 class Reader:
  cache=tmp_path/'cache/readings';key=None
  def __init__(self):self.images={}
  def read(self,image):
   key=hashlib.sha256(image).hexdigest()
   if key not in self.images:self.images[key]=len(self.images)
   return {'answer':copy.deepcopy(items[self.images[key]][2]),'cached':True,'new_cost_usd':0}
  def read_marker(self,image):return {'label':'3','uncertain':False,'cached':True,'new_cost_usd':0,'request_dispatched':False}
 path=tmp_path/'synthetic-neighbor.epub'
 try:report=vp.convert(doc,str(path),Reader())
 finally:doc.close()
 assert calls,'Fault must reach the actual SourceDisplay.pixmap operation inside new numeric-role helper'
 assert build_epub.validate(path)==[]
 import zipfile
 from xml.etree import ElementTree as ET
 with zipfile.ZipFile(path)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  images={name for name in z.namelist()if name.startswith('OEBPS/images/')}
 assert h.h.body_texts(docs).count(healthy)==1
 cases=report['degraded_units'];assert cases and all(case['page']==0 for case in cases)
 assert any(event.get('stage')=='page_compilation'and event.get('error_type')=='ValueError'for event in report['unit_recovery_events'])
 assert any('synthetic numeric role original pixel operation failure'in case.get('reason','')for case in cases)
 controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.h.NS)if a.get('class')=='source-disclosure-ref'];assert controls
 for name,a in controls:
  target_file,target=h.h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
  assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.h.NS))
  assert any(h.h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in nodes[0].findall('.//x:a',h.h.NS)if '#'in(back.get('href')or ''))
