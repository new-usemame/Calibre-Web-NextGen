"""Synthetic generated-asset metadata faults: original source/page ownership stays sound.
Three public conversion positives reach actual strict recovery predicates. Three lower
strict negatives retain fatal behavior when the page/source authority cannot be proved.
Synthetic text and geometry; no request transport, original raster mutation or guessed filename owner.
"""
import copy,json,re,sys,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,skeleton,unit_recovery,vision_pipeline as vp

def tag(n):return n.tag.rsplit('}',1)[-1]
def visible(n):return ' '.join(''.join(n.itertext()).split())

class OfflineReader:
 def __init__(self,path):self.cache=path;path.mkdir();self.dispatches=0
 def read(self,*args,**kwargs):self.dispatches+=1;raise AssertionError('No synthetic native reader dispatch')
 def read_marker(self,*args,**kwargs):self.dispatches+=1;raise AssertionError('No synthetic marker dispatch')

def source_fixture(tmp_path,art_pages=(1,)):
 doc=pymupdf.open();pages={};healthy={};figures=[];proof=[]
 for pno in range(3):
  page=doc.new_page(width=600,height=800);pages[pno]=[];healthy[pno]=[]
  for row in range(6):
   value=f'Synthetic leaf {pno} calibration row {row} remains a complete sentence.'
   page.insert_text((70,80+row*22),value,fontsize=12)
   span=next(s for b in page.get_text('dict')['blocks'] for line in b.get('lines',[]) for s in line['spans'] if s['text']==value)
   box=tuple(span['bbox']);pages[pno].append(assemble.Element('p',runs=[['t',value]],pno=pno,bbox=box,line_boxes=[box]));healthy[pno].append(value)
  if pno in art_pages:
   box=(90,310,510,510)
   page.draw_rect(box,color=(0,0,0),width=1)
   for x in (160,230,300,370,440):page.draw_line((x,310),(x,510),color=(0,0,0),width=1)
   for y in (350,390,430,470):page.draw_line((90,y),(510,y),color=(0,0,0),width=1)
   pages[pno].append(assemble.Element('fig',pno=pno,bbox=box,placed='geometry'))
   figures.append(dict(pno=pno,bbox=box,found='native'))
  assert page.get_images(full=True)==[] and vp.clean_native(page)
  proof.append(dict(page=pno,rect=list(page.rect),words=[dict(text=w[4],bbox=list(w[:4])) for w in page.get_text('words')],paths=[list(d['rect']) for d in page.get_drawings()],elements=[dict(kind=e.kind,bbox=e.bbox) for e in pages[pno]]))
 book=assemble.Book(elements=[e for es in pages.values() for e in es],pages=pages,figures=figures,style=skeleton.BookStyle(body_size=12,page_count=3))
 (tmp_path/'synthetic-original.pdf').write_bytes(doc.tobytes());(tmp_path/'source-ownership-proof.json').write_text(json.dumps(dict(pages=proof,prepared_figures=figures),indent=2)+'\n')
 return doc,book,healthy

@pytest.mark.parametrize('fault',['unidentifiable_filename','missing_ordinal','shared_generated_reference'])
def test_admitted_native_fragments_contain_generated_asset_metadata_fault(tmp_path,monkeypatch,fault):
 owned_pages=(0,1) if fault=='shared_generated_reference' else (1,)
 doc,book,healthy=source_fixture(tmp_path,owned_pages);before=copy.deepcopy(book);reader=OfflineReader(tmp_path/'cache')
 source_src='images/fig_p0001_0.jpg';fault_src='images/generated-art-reference.jpg' if fault!='missing_ordinal' else 'images/fig_p0001_99.jpg'
 original=build_epub._page_fragment;emitted=[];reached=[]
 def publish(subject,pno,*args,**kwargs):
  fragment=original(subject,pno,*args,**kwargs)
  original_src=f'images/fig_p{pno:04d}_0.jpg'
  if pno in owned_pages and original_src in fragment:
   fragment=fragment.replace(original_src,fault_src)
   emitted.append(dict(page=pno,original_src=original_src,generated_src=fault_src,occurrences=fragment.count(fault_src)))
   (tmp_path/'emitted-fragment-proof.json').write_text(json.dumps(emitted,indent=2)+'\n')
  return fragment
 monkeypatch.setattr(build_epub,'_page_fragment',publish)
 messages={'Unidentifiable native figure source','Unknown native figure source identity'};old=sys.gettrace()
 def trace(frame,event,arg):
  if event=='exception' and frame.f_code.co_name=='_recover_native_figures' and str(arg[1]) in messages:
   reached.append(dict(line=frame.f_lineno,file=frame.f_code.co_filename,reason=str(arg[1]),src=frame.f_locals.get('src'),owned_fragment_pages=[p for p,h in frame.f_locals.get('html_native',{}).items() if fault_src in h]))
   (tmp_path/'actual-original-predicate-reach.json').write_text(json.dumps(reached,indent=2)+'\n')
  return trace
 out=tmp_path/'actual.epub'
 try:
  sys.settrace(trace);report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
 finally:sys.settrace(old);doc.close()
 assert emitted and set(e['page'] for e in emitted)==set(owned_pages) and all(e['occurrences']==1 for e in emitted),'Fault changes generated metadata only inside actual admitted native SOURCE fragments'
 assert reached and all(r['owned_fragment_pages']==list(owned_pages) for r in reached),'Actual original recovery predicate has exactly the proved selected SOURCE fragment owner set'
 assert reader.dispatches==0 and book==before
 assert build_epub.validate(out)==[] and set(map(int,report['page_homes']))=={0,1,2}
 units=report['degraded_units'];assert units and set(u['page'] for u in units)==set(owned_pages),'Exactly the proved SOURCE owner superset must degrade; ambiguity cannot abort or guess one'
 assert any('native_figure' in u['stage'] and any(m in u['reason'] for m in messages) for u in units),'Original native figure failure must be explicitly reported, never silent omission'
 with zipfile.ZipFile(out) as z:
  roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')]
  paragraphs=[visible(n) for root in roots for n in root.iter() if tag(n)=='p' and not n.get('class','').startswith('source-')]
  for pno in set(range(3))-set(owned_pages):
   for value in healthy[pno]:assert paragraphs.count(value)==1,'Unaffected healthy neighbors stay live exactly once'
  assert not any(tag(n)=='img' and n.get('src')==fault_src for root in roots for n in root.iter()),'Broken generated image cannot survive packaging'
  for unit in units:
   target=unit['source_reference'];nodes=[n for root in roots for n in root.iter() if n.get('id')==target]
   assert len(nodes)==1 and any(tag(n)=='img' for n in nodes[0].iter())
   assert any(tag(n)=='a' and visible(n)=='Return' for n in nodes[0].iter())
   assert any(tag(n)=='a' and n.get('href','').split('#')[-1]==target for root in roots for n in root.iter())
 (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')

@pytest.mark.parametrize('authority',['unowned','foreign_selected_page','unavailable_original'])
def test_unproved_generated_asset_page_or_source_authority_remains_fatal(tmp_path,authority):
 doc,book,_=source_fixture(tmp_path);reader=OfflineReader(tmp_path/'cache');src='images/generated-art-reference.jpg'
 html='<figure><img src="'+src+'"/></figure>'
 fragments={} if authority=='unowned' else {1:html}
 controller=unit_recovery.Controller(reader,[0,2] if authority=='foreign_selected_page' else [0,1,2],tmp_path/'staging')
 class NoWrites:
  def image(self,*a,**k):raise AssertionError('Unproved authority must not write an asset')
  source_image=image
 try:
  with unit_recovery.activated(controller):
   with pytest.raises(unit_recovery.SourceIntegrityError):
    vp._recover_native_figures([src],fragments,book,None if authority=='unavailable_original' else doc,NoWrites(),{}, {})
 finally:doc.close()
 assert reader.dispatches==0 and controller.degraded=={} and controller.events==[],'Unproved SOURCE authority is fatal, not an inferred degradation owner'
