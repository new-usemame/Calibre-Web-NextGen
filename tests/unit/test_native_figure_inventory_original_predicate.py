"""Exact native inventory raise reach; synthetic cached Book, no fault injection."""
import copy,json,sys,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,skeleton,vision_pipeline as vp

def tag(n):return n.tag.rsplit('}',1)[-1]
def text(n):return ' '.join(''.join(n.itertext()).split())

@pytest.mark.parametrize('inventory',['missing','duplicate'])
def test_existing_native_figure_inventory_failure_degrades_only_its_source_page(tmp_path,inventory):
 doc=pymupdf.open();pages={};healthy={};figures=[];proof=[]
 for pno in range(3):
  page=doc.new_page(width=600,height=800);pages[pno]=[];healthy[pno]=[]
  for row in range(6):
   value=f'Synthetic leaf {pno} calibration row {row} remains a complete sentence.'
   page.insert_text((70,80+row*22),value,fontsize=12)
   span=next(s for b in page.get_text('dict')['blocks']for l in b.get('lines',[])for s in l['spans']if s['text']==value)
   box=tuple(span['bbox']);pages[pno].append(assemble.Element('p',runs=[['t',value]],pno=pno,bbox=box,line_boxes=[box]));healthy[pno].append(value)
  if pno==1:
   for box in ((90,290,510,425),(90,490,510,625)):
    page.draw_rect(box,color=(0,0,0),width=1)
    for x in (160,230,300,370,440):page.draw_line((x,box[1]),(x,box[3]),color=(0,0,0),width=1)
    for y in (box[1]+35,box[1]+70,box[1]+105):page.draw_line((box[0],y),(box[2],y),color=(0,0,0),width=1)
   owned=(90,290,510,425);pages[pno].append(assemble.Element('fig',pno=pno,bbox=owned,placed='geometry'))
   if inventory=='duplicate':figures.extend([dict(pno=pno,bbox=owned,found='native')for _ in range(2)])
  assert page.get_images(full=True)==[] and vp.clean_native(page)
  proof.append(dict(page=pno,source_words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')],vector_paths=[list(d['rect'])for d in page.get_drawings()],elements=[dict(kind=e.kind,bbox=e.bbox,text=e.text)for e in pages[pno]]))
 book=assemble.Book(elements=[e for es in pages.values()for e in es],pages=pages,figures=figures,style=skeleton.BookStyle(body_size=12,page_count=3));before=copy.deepcopy(book)
 (tmp_path/'original-source.pdf').write_bytes(doc.tobytes());(tmp_path/'source-proof.json').write_text(json.dumps(dict(pages=proof,prepared_figures=figures),indent=2)+'\n')
 class OfflineReader:
  cache=tmp_path/'offline-cache';dispatches=0
  def read(self,*a,**kw):self.dispatches+=1;raise AssertionError('No synthetic native paid/request route')
  def read_marker(self,*a,**kw):self.dispatches+=1;raise AssertionError('No marker request route')
 reader=OfflineReader();reader.cache.mkdir();reached=[];old=sys.gettrace()
 def trace(frame,event,arg):
  if event=='exception' and frame.f_code.co_name=='_native_publication_ownership':
   kind,value,tb=arg
   if kind is ValueError and str(value)=='Native figure source inventory is not one-to-one':
    reached.append(dict(page=frame.f_locals['pno'],line=frame.f_lineno,source_path=frame.f_code.co_filename,match_count=len(frame.f_locals['matches']),new_vector_additions=len(frame.f_locals['additions'])))
    (tmp_path/'exact-original-predicate-reach.json').write_text(json.dumps(reached,indent=2)+'\n')
  return trace
 try:
  sys.settrace(trace);out=tmp_path/'actual.epub';report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
 finally:sys.settrace(old);doc.close()
 assert reached and all(r['page']==1 and r['match_count']==(0 if inventory=='missing'else 2)and r['new_vector_additions']==1 for r in reached),'Reach must be actual original one-to-one predicate after one new vector, no injected exception'
 assert reader.dispatches==0 and book==before
 assert build_epub.validate(out)==[] and set(map(int,report['page_homes']))=={0,1,2}
 units=report['degraded_units'];assert units and all(u['page']==1 for u in units)
 assert any(u['stage'].startswith('native_publication') and 'inventory is not one-to-one' in u['reason']for u in units),'Actual original failure stage/reason must remain reported'
 assert any(e.get('stage','').startswith('native_publication')for e in report['unit_recovery_events'])
 with zipfile.ZipFile(out)as z:
  roots={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
  pars=[text(n)for root in roots.values()for n in root.iter()if tag(n)=='p' and not n.get('class','').startswith('source-')]
  for pno in (0,2):
   for value in healthy[pno]:assert pars.count(value)==1,'Unrelated healthy neighbors must remain live once'
  for unit in units:
   dest=unit['source_reference'];targets=[n for root in roots.values()for n in root.iter()if n.get('id')==dest]
   assert len(targets)==1 and any(tag(n)=='img'for n in targets[0].iter())
   assert any(tag(n)=='a' and text(n)=='Return'for n in targets[0].iter())
   assert any(tag(n)=='a' and n.get('href','').split('#')[-1]==dest for root in roots.values()for n in root.iter())
 (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')
