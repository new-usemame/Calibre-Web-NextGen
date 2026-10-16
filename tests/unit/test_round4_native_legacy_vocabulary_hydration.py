"""Independent legacy cached native source-vocabulary hydration controls."""
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import copy,pymupdf,zipfile,pytest
from cps.services.reflow import assemble,build_epub,vision_pipeline,vision_pixels
NS={'x':'http://www.w3.org/1999/xhtml'}
SOURCE='Read the valley reference in this ordinary native sentence.'
DEST='The destination preserves its ordinary native body sentence.'
class OfflineReader:
 def __init__(self,p):self.cache=p;p.mkdir();self.calls=0
 def read(self,*a):self.calls+=1;raise AssertionError('No model request')
 def read_marker(self,*a):raise AssertionError('No marker request')

def fixture(tmp_path,monkeypatch,mode):
 if mode=='known_whole':left='Bright sunshine warms the plants while they turn toward sun-';right='shine for another ordinary growing season.';words=('sunshine',);compounds=();expected='sunshine for another'
 elif mode=='compound_over_whole':left='A deep-seated root has a deepseated label and another deep-';right='seated root continues through this ordinary soil.';words=('deepseated',);compounds=('deep-seated',);expected='another deep-seated root'
 else:left='The plants have roots that remain unusually deep-';right='seated through this ordinary soil and growing season.';words=();compounds=();expected='unusually deep-seated through'
 doc=pymupdf.open();pages={}
 try:
  for n in range(2):
   page=doc.new_page(width=431.97,height=647.97)
   contents=[(SOURCE,105),(left,590)] if n==0 else [(right,100),(DEST,150)]
   rows=[]
   for text,y in contents:
    page.insert_text((54,y),text,fontsize=11)
    rows.append(assemble.Element('p',runs=[['t',text]],pno=n,bbox=(54,y-14,400,y+4)))
   pages[n]=rows
  nav=dict(id='pdfgoto_p0000_x47',kind=1,status='resolved',pno=0,source_element=0,source_item=None,source_offset=SOURCE.index('valley'),source_extent=6,dest_page=1,dest_element=1,dest_item=None,dest_offset=DEST.index('destination'),dest_note=None,label='valley',dest_point=[54,150])
  book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages,source_navigation=[nav],page_wrap_words=(),page_wrap_compounds=())
  reader=OfflineReader(tmp_path/'unused-cache');out=tmp_path/'native-wrap.epub'
  assert book.page_wrap_words==() and book.page_wrap_compounds==(), 'Legacy cached Book has no vocabulary evidence'
  if mode=='scanned_only':
   scan=pymupdf.open();pg=scan.new_page(width=431.97,height=647.97);text='Deepseated labels occur only on this separately scanned ordinary source page.';pg.insert_text((54,100),text,fontsize=11)
   optical=[{'text':w[4],'bbox':list(w[:4]),'confidence':99} for w in pg.get_text('words')]
   image=pg.get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png');scan.close();pg=doc.new_page(width=431.97,height=647.97);pg.insert_image(pg.rect,stream=image)
   reading={'blocks':[dict(id='scanned-body',text=text,role='body',marker='',bbox=[120,120,940,220],low_confidence=[],continues_previous=False,continues_next=False)]}
   monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(optical))
   def read(image):reader.calls+=1;return {'answer':copy.deepcopy(reading),'cached':True,'new_cost_usd':0}
   reader.read=read

  assert all(vision_pipeline.clean_native(doc[n]) for n in [0,1]);assert mode!='scanned_only' or not vision_pipeline.clean_native(doc[2])
  report=vision_pipeline.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
  assert reader.calls==int(mode=='scanned_only') and report['vision_pages']==([2] if mode=='scanned_only' else []) and report['native_pages']==[0,1]
  assert build_epub.validate(str(out))==[]
  with zipfile.ZipFile(out) as z:docs={name:ET.fromstring(z.read(name)) for name in z.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')}
  paragraphs=[' '.join(''.join(p.itertext()).split()) for root in docs.values() for p in root.findall('.//x:p',NS) if 'source-evidence-notice' not in p.get('class','').split()]
  refs=[(name,a) for name,root in docs.items() for a in root.findall('.//x:a',NS) if 'pdfgoto' in a.get('href','')];assert len(refs)==1
  name,ref=refs[0];assert ''.join(ref.itertext())=='valley';file,ident=ref.get('href').split('#',1);targetfile='OEBPS/'+file if file else name
  targets=[e for e in docs[targetfile].iter() if e.get('id')==ident];assert len(targets)==1 and (targets[0].tail or '').startswith('destination')
  source_ids=[e.get('id') for e in docs[name].iter() if 'pdfref_' in e.get('id','')];assert len(source_ids)==1
  assert any(a.get('href','').endswith('#'+source_ids[0]) and ''.join(a.itertext())=='Return' for root in docs.values() for a in root.findall('.//x:a',NS))
  owner=next(p for root in docs.values() for p in root.findall('.//x:p',NS) if ' '.join(''.join(p.itertext()).split()).startswith(left.split()[0]) and 'ordinary soil' in ''.join(p.itertext()) or 'Bright sunshine' in ''.join(p.itertext()) and 'another ordinary' in ''.join(p.itertext()))
  assert {s.get('data-source-page') for s in owner.iter() if s.get('data-source-page')}=={'0','1'},'Joined paragraph retains both physical source page owners'
  assert any(expected in p for p in paragraphs),'Only source-vocabulary-supported whole-word repair may delete a printed hyphen; compounds override ambiguous whole-word evidence'
 finally:doc.close()

def test_empty_legacy_native_cache_hydrates_whole_word_from_clean_original_source(tmp_path,monkeypatch):fixture(tmp_path,monkeypatch,'known_whole')
def test_empty_legacy_native_cache_keeps_source_compound_over_ambiguous_whole(tmp_path,monkeypatch):fixture(tmp_path,monkeypatch,'compound_over_whole')
def test_scanned_optical_word_cannot_earn_native_hyphen_deletion(tmp_path,monkeypatch):fixture(tmp_path,monkeypatch,'scanned_only')
