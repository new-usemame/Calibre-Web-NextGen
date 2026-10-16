"""Source-free actual words operation faults inside native list ownership guard."""
from types import SimpleNamespace
import inspect,json,re,zipfile,copy
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,assemble,skeleton,build_epub
HEALTHY='Independent healthy narrative remains live exactly once.'
NEIGHBOR='Unaffected neighbor paragraph remains independently live.'
def plain(n):return ' '.join(''.join(n.itertext()).split())
def tag(n):return n.tag.rsplit('}',1)[-1]
@pytest.mark.parametrize('fault_page',[1,2])
def test_native_list_original_words_failure_is_named_contained_and_source_visible(tmp_path,monkeypatch,fault_page):
 doc=pymupdf.open();pages={};elements=[]
 def row(pno,text,y,x=70):
  doc[pno].insert_text((x,y),text,fontsize=12)
  w=doc[pno].get_text('words');owned=[a for a in w if abs(a[3]-(y+3.588))<1.5]
  assert owned
  return (min(a[0] for a in owned),min(a[1] for a in owned),max(a[2] for a in owned),max(a[3] for a in owned))
 def el(pno,text,y,x=70,kind='p'):
  b=row(pno,text,y,x);e=assemble.Element(kind,runs=[['t',text]],pno=pno,bbox=b,line_boxes=[b],column=0)
  if kind=='list':e.list_items=[[['t',text]]];e.list_item_boxes=[[b]]
  pages[pno].append(e);elements.append(e);return e
 for pno in range(3):
  doc.new_page(width=600,height=800);pages[pno]=[]
  for i in range(6):el(pno,'Calibration account '+str(pno)+' row '+str(i)+' remains separate.',200+i*24)
 el(0,HEALTHY,430)
 el(1,'2. This continuing listed account requires the',746,kind='list')
 e=el(2,'remaining narrative to complete the explanation. 3. The fresh listed item is independent.',90,x=88)
 # Printed fresh item is a separate physical row, while cache combines them.
 pages[2].remove(e);elements.remove(e)
 # Rebuild original page2 to remove first construction of combined text.
 # This synthetic evidence uses exact page ink, not model-only geometry.
 replacement=pymupdf.open();rp=replacement.new_page(width=600,height=800)
 for i in range(6):rp.insert_text((70,200+i*24),'Calibration account 2 row '+str(i)+' remains separate.',fontsize=12)
 rp.insert_text((88,90),'remaining narrative to complete the explanation.',fontsize=12);rp.insert_text((70,122),'3. The fresh listed item is independent.',fontsize=12)
 doc.delete_page(2);doc.insert_pdf(replacement);replacement.close()
 rows=doc[2].get_text('words');a=[w for w in rows if w[1]<100];b=[w for w in rows if 100<w[1]<130]
 bbox=lambda ws:(min(w[0] for w in ws),min(w[1] for w in ws),max(w[2] for w in ws),max(w[3] for w in ws))
 e=assemble.Element('p',runs=[['t','remaining narrative to complete the explanation. 3. The fresh listed item is independent.']],pno=2,bbox=bbox(a+b),line_boxes=[bbox(a),bbox(b)],column=0);pages[2].insert(0,e)
 el(2,NEIGHBOR,430);elements=[e for pno in range(3) for e in pages[pno]]
 book=assemble.Book(elements=elements,pages=pages,notes=[],figures=[],style=skeleton.BookStyle(body_size=12,page_count=3));before=copy.deepcopy(book)
 doc.save(tmp_path/'synthetic-original.pdf');assert all(vp.clean_native(doc[p]) for p in range(3))
 class Offline:
  cache=tmp_path/'cache';calls=0
  def read(self,*a,**k):self.calls+=1;raise AssertionError('No network fixture reader')
  def read_marker(self,*a,**k):self.calls+=1;raise AssertionError('No marker request')
 reader=Offline();reader.cache.mkdir();actual=pymupdf.Page.get_text;reached=[]
 def operation(page,option='text',*args,**kwargs):
  if page.number==fault_page and option=='words' and any(f.function=='_native_list_head_ownership' for f in inspect.stack()):
   reached.append(page.number);raise ValueError('synthetic_native_list_original_words_unavailable')
  return actual(page,option,*args,**kwargs)
 monkeypatch.setattr(pymupdf.Page,'get_text',operation)
 out=tmp_path/'actual.epub'
 try:
  report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book));assert reached and reader.calls==0 and book==before
  assert build_epub.validate(out)==[]
  with zipfile.ZipFile(out) as z:roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
  live=[];nodes={};refs=[]
  def visit(n,aside=False):
   aside=aside or tag(n)=='aside'
   if n.get('id'):nodes[n.get('id')]=n
   if tag(n)=='a' and 'source-disclosure-ref' in n.get('class',''):refs.append(n)
   if not aside and tag(n) in ('p','li'):live.append(plain(n))
   for c in n:visit(c,aside)
  for root in roots:visit(root)
  visible=' '.join(live);assert visible.count(HEALTHY)==visible.count(NEIGHBOR)==1
  units=report.get('degraded_units',[]);assert any(u.get('stage')=='native_list_head_ownership' and 'synthetic_native_list_original_words_unavailable' in u.get('reason','') and u.get('page') in (1,2) for u in units)
  assert refs
  for r in refs:
   target=nodes[r.get('href','').split('#')[-1]];assert any(tag(n)=='img' for n in target.iter());assert any(tag(n)=='a' and 'Return' in plain(n) and n.get('href','').split('#')[-1]==r.get('id') for n in target.iter())
  (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2))
 finally:doc.close()
