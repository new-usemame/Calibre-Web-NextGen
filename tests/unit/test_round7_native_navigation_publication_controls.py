"""Independent original SOURCE-free native navigation publication controls.
PDF annotations and text rows are written for these tests; no model or OCR.
"""
import copy,re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,extract,skeleton,vision_pipeline as vp

def union(bs):return(min(b[0]for b in bs),min(b[1]for b in bs),max(b[2]for b in bs),max(b[3]for b in bs))
def text(n):return ' '.join(''.join(n.itertext()).split())
def tag(n):return n.tag.rsplit('}',1)[-1]
class Offline:
 def __init__(self,p):self.cache=p/'offline';self.cache.mkdir();self.dispatches=0
 def read(self,*a,**kw):self.dispatches+=1;raise AssertionError('Synthetic native controls forbid model dispatch')
 def read_marker(self,*a,**kw):self.dispatches+=1;raise AssertionError('Synthetic native controls forbid marker dispatch')
class Fixture:
 def __init__(self,tmp,case,navigation=True):
  self.tmp=tmp;self.doc=pymupdf.open();self.elements={};self.allrows={};self.figures=[]
  for page in range(2):
   self.doc.new_page(width=600,height=800);self.elements[page]=[];self.allrows[page]=[]
   for i in range(6):self.element(page,[self.row(page,f'Calibration specimen {page} row {i} is a complete sentence.',100+i*22)])
  self.label='7';self.reference_row=None;self.destination_row=None
  if case=='vector':
   p=self.doc[0];p.draw_rect((90,250,510,430));
   for i in range(1,5):p.draw_line((90+80*i,250),(90+80*i,430))
   p.draw_line((90,250),(510,430));self.vector=True
  else:self.vector=False
  for page,transform in [(0,case in ['outgoing','reorder','missingraw','direct']),(1,case=='incoming')]:
   y=470 if page==0 and self.vector else 320
   a=self.row(page,'The synthetic observer follows the unfinished',y)
   b=self.row(page,'ordinary reference 7 through the continuing original account.' if page==0 else 'destination passage with a literal original viewport.',y+20)
   c=self.row(page,'The final original row completes this ordinary paragraph.',y+40)
   if page==0:self.reference_row=b
   else:self.destination_row=b
   if transform:
    if case=='reorder':self.element(page,[a,c]);self.element(page,[b])
    else:self.element(page,[a]);self.element(page,[b,c])
   else:self.element(page,[a,b,c])
  if navigation:
   rect=self.doc[0].search_for('reference 7')[0];prefix=self.doc[0].search_for('reference')[0];rect.x0=prefix.x1+1
   self.doc[0].insert_link(dict(kind=pymupdf.LINK_GOTO,from_=rect,page=1,to=pymupdf.Point(self.destination_row['bbox'][0],(self.destination_row['bbox'][1]+self.destination_row['bbox'][3])/2))) if False else self.doc[0].insert_link({'kind':pymupdf.LINK_GOTO,'from':rect,'page':1,'to':pymupdf.Point(self.destination_row['bbox'][0],(self.destination_row['bbox'][1]+self.destination_row['bbox'][3])/2)})
  self.doc.save(tmp/'original.pdf');self.doc.close();self.doc=pymupdf.open(tmp/'original.pdf')
  self.raw=[extract.read_page(self.doc,n,keep_char_boxes=True)for n in range(2)]
  self.book=assemble.Book(pages=self.elements,elements=sum(self.elements.values(),[]),style=skeleton.BookStyle(body_size=12,page_count=2))
  assemble._bind_source_navigation(self.book,self.raw)
  self.original_nav=copy.deepcopy(self.book.source_navigation)
  if navigation:assert len(self.original_nav)==1 and self.original_nav[0]['status']=='resolved','Fixture must prove one original annotated source and destination before any transformation'
 def row(self,n,t,y,x=70):
  p=self.doc[n];p.insert_text((x,y),t,fontname='helv',fontsize=12)
  ls=[ln for b in p.get_text('dict')['blocks']for ln in b.get('lines',[])if ''.join(s['text']for s in ln['spans'])==t];assert len(ls)==1
  r={'text':t,'bbox':tuple(ls[0]['bbox'])};self.allrows[n].append(r);return r
 def element(self,n,rows):
  self.elements[n].append(assemble.Element('p',pno=n,runs=[['t',' '.join(r['text']for r in rows)]],bbox=union([r['bbox']for r in rows]),line_boxes=[r['bbox']for r in rows],column=0))
 def publish(self,raw=True):
  before=copy.deepcopy(self.book);reader=Offline(self.tmp);prepared=SimpleNamespace(book=self.book)
  if raw:prepared.raw_pages=self.raw
  out=self.tmp/'actual.epub';report=vp.convert(self.doc,str(out),reader,native_prepared=prepared)
  assert reader.dispatches==0 and self.book==before,'Conversion must not mutate caller preparation or call model'
  with zipfile.ZipFile(out)as z:roots=[ET.fromstring(z.read(n))for n in z.namelist()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
  return report,roots
 def close(self):self.doc.close()

def assert_live_navigation(f,report,roots):
 assert report['degraded_unit_count']==0,'Originally resolved native annotation must stay live; stale element indices must not degrade healthy SOURCE prose'
 rid=f.original_nav[0]['id'];origin='pdfref_'+rid.removeprefix('pdfgoto_')
 source=[n for r in roots for n in r.iter()if n.get('id')==origin];destination=[n for r in roots for n in r.iter()if n.get('id')==rid]
 assert len(source)==len(destination)==1,'Original PDF source and destination anchors must each survive exactly once'
 linked=[n for r in roots for n in r.iter()if tag(n)=='a' and(n.get('href')or'').split('#')[-1]==rid]
 assert len(linked)==1 and text(linked[0])=='7','The physically annotated character must remain the actual link label, at its original sentence position'
 pars=[n for r in roots for n in r.iter()if tag(n)=='p' and any(child is source[0] for child in n.iter())]
 assert any('ordinary reference 7 through the continuing original account.' in text(p)for p in pars),'Outgoing link must belong to the complete original reference sentence, not stale character position'
 backs=[n for r in roots for n in r.iter()if tag(n)=='a'and(n.get('href')or'').split('#')[-1]==origin]
 assert backs,'Original destination requires an exact backlink to the surviving source reference'

@pytest.mark.parametrize('case',['outgoing','incoming','reorder','vector'])
def test_public_converter_preserves_original_navigation_through_native_publication(tmp_path,case):
 f=Fixture(tmp_path,case)
 try:
  report,roots=f.publish();assert_live_navigation(f,report,roots)
 finally:f.close()

def test_no_raw_source_navigation_proof_retains_original_units_and_actions(tmp_path):
 f=Fixture(tmp_path,'missingraw')
 try:
  report,roots=f.publish(raw=False);assert_live_navigation(f,report,roots)
 finally:f.close()

def test_direct_native_helper_without_raw_pages_retains_navigation_element_and_offset_owners(tmp_path):
 f=Fixture(tmp_path,'direct')
 try:
  old=copy.deepcopy(f.book);vp._native_publication_ownership(f.doc,f.book,[0,1])
  assert f.book.pages==old.pages and f.book.source_navigation==old.source_navigation,'Without exact original raw annotation proof, standalone publication must retain Source action ownership rather than mutate indexed units'
 finally:f.close()

@pytest.mark.parametrize('case',['outgoing','vector'])
def test_native_no_navigation_positive_path_still_repairs_original_paragraph_or_vector(tmp_path,case):
 f=Fixture(tmp_path,case,navigation=False)
 try:
  report,roots=f.publish();assert report['degraded_unit_count']==0
  assert report.get('source_navigation',[])==[]
  if case=='outgoing':
   ps=[n for r in roots for n in r.iter()if tag(n)=='p'];assert any('The synthetic observer follows the unfinished ordinary reference 7 through the continuing original account.' in text(n)for n in ps)
  else:assert any(tag(n)=='figure'for r in roots for n in r.iter()),'Complete unambiguous original vector artwork still belongs in normal reading flow without NAV'
 finally:f.close()
