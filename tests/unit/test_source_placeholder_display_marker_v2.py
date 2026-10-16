"""Prepatch SOURCE-free actual EPUB controls: body glyph is not footer label.

Original raster ink and original-word coordinates are synthetic. Model placeholders
name a correct owner but do not supply its printed in-body display marker. Text
is written for these tests; no network requests or measurement changes are used.
"""
import copy,importlib.util,posixpath,re,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
s=importlib.util.spec_from_file_location('r7_placeholder_display_ink',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
NOTE='A distinct note preserves its exact source owner.'
BODY='A bright ruby carries clear light. Every healthy word remains once.'

def source_ink(page,box):
 pix=page.get_pixmap(matrix=pymupdf.Matrix(12,12),clip=pymupdf.Rect(box),colorspace=pymupdf.csGRAY,alpha=False)
 dark=[(i%pix.width,i//pix.width)for i,v in enumerate(pix.samples)if v<180]
 assert dark,'Every supplied marker candidate must contain genuine original ink'
 return [(pix.x+min(x for x,y in dark))/12,(pix.y+min(y for x,y in dark))/12,(pix.x+max(x for x,y in dark)+1)/12,(pix.y+max(y for x,y in dark)+1)/12]

def fixture(body_marker='3',footer='33',mode='placeholder'):
 p=h.InkPage();font=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
 prefix='A bright ruby';x=24+pymupdf.get_text_length(prefix,fontsize=9)
 p.page.insert_text((24,55),prefix,fontsize=9);glyphs=[];px=x
 physical=body_marker if mode not in ('absent','unobserved') else body_marker if mode=='unobserved' else ''
 for char in physical:
  p.page.insert_text((px,51),char,fontsize=5,fontname='unicode');width=font.text_length(char,fontsize=5)
  glyphs.append(dict(label=char,bbox=source_ink(p.page,[px-.2,44,px+width+.5,53]),kind='bare'));px+=width+.2
 # Multi-digit labels are one physical slot, not independent reference instances.
 if body_marker.isdecimal() and len(body_marker)>1 and glyphs:
  glyphs=[dict(label=body_marker,bbox=[glyphs[0]['bbox'][0],min(g['bbox'][1]for g in glyphs),glyphs[-1]['bbox'][2],max(g['bbox'][3]for g in glyphs)],kind='bare')]
 p.page.insert_text((px+3,55),' carries clear light.',fontsize=9)
 p.page.insert_text((24,69),'Every healthy word remains once.',fontsize=9)
 model=prefix+(body_marker if mode=='literal_plus_placeholder' else '')+'<n:n1> carries clear light. Every healthy word remains once.'
 p.blocks.append(h.model_block(model,'body','body',[23,41,282,74]))
 p.page.insert_text((24,330),footer+' '+NOTE,fontsize=7,fontname='unicode')
 p.blocks.append(h.model_block(NOTE,'n1','footnote',[23,318,282,335],marker=footer))
 image,words,answer=p.finish()
 if mode=='unobserved':
  words=[w for w in words if not (w['bbox'][1]<80 and w['text']==body_marker)]
  for w in words:
   if w['bbox'][1]<80 and body_marker in w['text']:w['text']=w['text'].replace(body_marker,'')
  glyphs=[]
 return image,words,answer,glyphs

def convert(prepared,tmp_path,monkeypatch,marker_response=None):
 image,words,answer,glyphs=prepared;doc=pymupdf.open();page=doc.new_page(width=h.W,height=h.H);page.insert_image(page.rect,stream=image)
 monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(words))
 observed=[]
 def candidates(display,box,cache):observed.append(('original_marker_candidates',display.pno));return copy.deepcopy(glyphs)
 monkeypatch.setattr(vision_pixels,'raised_markers',candidates)
 class Reader:
  cache=tmp_path/'cache/readings';calls=0;marker_calls=0
  def read(self,image):self.calls+=1;return {'answer':copy.deepcopy(answer),'cached':True,'new_cost_usd':0}
  def read_marker(self,image):
   self.marker_calls+=1
   return dict(marker_response if marker_response is not None else {'label':glyphs[0]['label']if len(glyphs)==1 else '', 'uncertain':len(glyphs)!=1},cached=True,new_cost_usd=0)
 reader=Reader();path=tmp_path/'synthetic.epub'
 try:report=vp.convert(doc,str(path),reader)
 finally:doc.close()
 assert reader.calls==1 and build_epub.validate(path)==[]
 assert set(map(int,report['page_homes']))=={0}
 with zipfile.ZipFile(path)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  images={name for name in z.namelist()if name.startswith('OEBPS/images/')}
 return report,docs,images,observed

def assert_owner_and_body(docs,count=1):
 refs,notes=h.assert_exact_returns(docs,count)
 assert all(ref.get('href').endswith('#v4-note-0-n1')for _,ref in refs),'Correct note owner may not change to fit body display glyph'
 owned=[note for _,note in notes if note.get('id')=='v4-note-0-n1'];assert len(owned)==1 and NOTE in ''.join(owned[0].itertext())
 assert h.body_texts(docs)==[BODY],'Every healthy BODY word must remain exactly once without duplicate literal marker'
 return refs

def assert_visible_source_uncertainty(report,docs,images):
 controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref']
 assert controls,'Ambiguous/missing body marker evidence requires visible SOURCE disclosure, not silent catalogue display'
 for name,a in controls:
  target_file,target=h.destination(name,a.get('href'));assert target_file in docs
  nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
  target_node=nodes[0]
  assert any('OEBPS/'+img.get('src')in images for img in target_node.findall('.//x:img',h.NS)),'SOURCE uncertainty must retain original raster evidence'
  assert any(h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in target_node.findall('.//x:a',h.NS)if back.get('href')and '#'in back.get('href')),'SOURCE disclosure Return must reach exact original control'
 assert NOTE in ' '.join(''.join(root.itertext())for root in docs.values()),'Unprovable marker display cannot silently discard its correct note'
 assert h.body_texts(docs)==[BODY],'Ambiguous marker may not degrade or alter otherwise proved healthy BODY'
 listed=report.get('note_marker_disclosures',[])
 assert isinstance(listed,list) and listed,'Visible marker uncertainty must be explicitly listed in book report, separate from confident note ownership'
 assert any(isinstance(case,dict) and case.get('page')==0 for case in listed),'Marker uncertainty report must preserve actual source page ownership'

@pytest.mark.parametrize('body_marker,footer',[('3','33'),('12','2'),('*','†')])
def test_placeholder_uses_proved_original_body_glyph_not_footer_label(body_marker,footer,tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture(body_marker,footer),tmp_path,monkeypatch)
 refs=assert_owner_and_body(docs)
 assert ''.join(refs[0][1].itertext())==body_marker,'SOURCE body glyph must be displayed; footer/catalogue label is not evidence for this occurrence'

@pytest.mark.parametrize('marker',['7','*'])
def test_matching_body_footer_marker_preserves_exact_target_and_Return(marker,tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture(marker,marker),tmp_path,monkeypatch)
 refs=assert_owner_and_body(docs);assert ''.join(refs[0][1].itertext())==marker
 assert report['degraded_units']==[],'Healthy unambiguous matching source marker must not degrade its unit'

@pytest.mark.parametrize('mode',['unobserved','absent'])
def test_unproved_placeholder_display_requires_visible_original_evidence(mode,tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture('3','33',mode),tmp_path,monkeypatch)
 assert_visible_source_uncertainty(report,docs,images)


def test_two_different_original_glyphs_for_one_placeholder_are_not_guessed(tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture('3*','33'),tmp_path,monkeypatch)
 assert_visible_source_uncertainty(report,docs,images)

@pytest.mark.parametrize('marker,footer',[('7','7'),('3','33')])
def test_literal_and_placeholder_same_single_original_glyph_reconcile_once(marker,footer,tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture(marker,footer,'literal_plus_placeholder'),tmp_path,monkeypatch)
 refs=assert_owner_and_body(docs);assert ''.join(refs[0][1].itertext())==marker



def test_missing_optical_glyph_verified_original_slot_preserves_reader_label(tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture('3','33','unobserved'),tmp_path,monkeypatch,marker_response={'label':'3','uncertain':False})
 refs=assert_owner_and_body(docs);assert ''.join(refs[0][1].itertext())=='3','Confident bounded ORIGINAL slot verification may supply body label when whole-word optical glyph is missing'

@pytest.mark.parametrize('response',[{'label':'','uncertain':True},{'label':'?','uncertain':False}])
def test_missing_optical_glyph_unusable_verification_keeps_disclosed_uncertainty(response,tmp_path,monkeypatch):
 report,docs,images,observed=convert(fixture('3','33','unobserved'),tmp_path,monkeypatch,marker_response=response)
 assert_visible_source_uncertainty(report,docs,images)


def test_two_genuine_printed_slot_occurrences_keep_both_actions(tmp_path,monkeypatch):
 p=h.InkPage();prefixes=[('A bright ruby',55,' carries clear light.'),('A calm sapphire',85,' retains gentle color.')]
 model=[];glyphs=[]
 for prefix,y,following in prefixes:
  p.page.insert_text((24,y),prefix,fontsize=9);x=24+pymupdf.get_text_length(prefix,fontsize=9)
  p.page.insert_text((x,y-4),'7',fontsize=5);glyphs.append(dict(label='7',bbox=source_ink(p.page,[x-.2,y-10,x+4,y-2]),kind='bare'))
  p.page.insert_text((x+6,y),following,fontsize=9);model.append(prefix+'<n:n1>'+following)
 p.blocks.append(h.model_block(' '.join(model),'body','body',[23,41,282,90]))
 p.text('7 '+NOTE,'n1','footnote',y=330,size=7,marker='7',model=NOTE)
 image,words,answer=p.finish();report,docs,images,observed=convert((image,words,answer,glyphs),tmp_path,monkeypatch)
 refs,notes=h.assert_exact_returns(docs,2)
 assert all(''.join(ref.itertext())=='7' and ref.get('href').endswith('#v4-note-0-n1')for _,ref in refs)
 assert h.body_texts(docs)==['A bright ruby carries clear light. A calm sapphire retains gentle color.']


def test_actual_marker_source_operation_failure_is_unit_contained(tmp_path,monkeypatch):
 before='A healthy emerald paragraph retains every ordinary source word.'
 after='A healthy sapphire paragraph preserves its independent source owner.'
 prepared=[]
 for text in [before,after]:
  p=h.InkPage();p.text(text,'healthy','body',y=55);prepared.append(p.finish())
 damaged=fixture('7','7','literal_plus_placeholder');prepared.insert(1,damaged[:3])
 doc=pymupdf.open()
 for image,words,answer in prepared:
  page=doc.new_page(width=h.W,height=h.H);page.insert_image(page.rect,stream=image)
 monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
 reached=[]
 def failure(display,box,cache):
  assert display.pno==1,'SOURCE operation failure may not be assigned to a foreign healthy page'
  reached.append(display.pno);raise ValueError('synthetic original marker operation unavailable')
 monkeypatch.setattr(vision_pixels,'raised_markers',failure)
 class Reader:
  cache=tmp_path/'cache/readings';calls=0
  def read(self,image):
   value=prepared[self.calls][2];self.calls+=1;return {'answer':copy.deepcopy(value),'cached':True,'new_cost_usd':0}
 reader=Reader();path=tmp_path/'contained.epub'
 try:report=vp.convert(doc,str(path),reader)
 finally:doc.close()
 assert reached==[1] and reader.calls==3 and build_epub.validate(path)==[]
 assert set(map(int,report['page_homes']))=={0,1,2}
 assert report['degraded_units'] and all(case['page']==1 for case in report['degraded_units'])
 assert any(case.get('stage')=='page_compilation' and 'marker operation unavailable' in case.get('reason','')for case in report['degraded_units'])
 with zipfile.ZipFile(path)as z:
  docs={name:ET.fromstring(z.read(name))for name in z.namelist()if name.endswith('.xhtml')and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
  live=' '.join(h.body_texts(docs));assert live.count(before)==live.count(after)==1
  for case in report['degraded_units']:
   src=case['source_image'];assert 'OEBPS/'+src in z.namelist()
   assert any(img.get('src')==src for root in docs.values()for img in root.findall('.//x:img',h.NS))
  controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref'];assert controls
  for name,a in controls:
   target_file,target=h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
   assert any(h.destination(target_file,b.get('href'))==(name,a.get('id'))for b in nodes[0].findall('.//x:a',h.NS)if b.get('href')and '#'in b.get('href'))
