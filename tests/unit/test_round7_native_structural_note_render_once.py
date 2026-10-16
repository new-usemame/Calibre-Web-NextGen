"""Source-free public first/last/repeated list and display-heading note origins."""
import copy,re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,skeleton,vision_pipeline as vp,build_epub
FIRST='1. The independently printed initial item is complete.'
LAST='2. The independently printed final item is complete.'
HEALTHY='Healthy neighboring narrative remains live once.'
def tag(n):return n.tag.rsplit('}',1)[-1]
def plain(n):return ' '.join(''.join(n.itertext()).split())
@pytest.mark.parametrize('case',['first','last','repeated','display_heading'])
def test_native_structural_note_atoms_render_once_with_registered_targets_and_number_only_backlink(tmp_path,case):
 doc=pymupdf.open();doc.new_page(width=600,height=800);elements=[]
 def row(text,y,size=12):
  doc[0].insert_text((70,y),text,fontsize=size)
  spans=[s for b in doc[0].get_text('dict')['blocks']for ln in b.get('lines',[])for s in ln['spans']if s['text']==text];assert len(spans)==1
  return tuple(spans[0]['bbox'])
 for i in range(6):
  text='Calibration specimen '+str(i)+' is independently complete.';b=row(text,200+i*24);elements.append(assemble.Element('p',runs=[['t',text]],pno=0,bbox=b,line_boxes=[b]))
 glyphs=[]
 def printed(text,y,mark):
  b=row(text,y);runs=[['t',text]]
  if mark:
   doc[0].insert_text((b[2]+1,y-6),'1',fontsize=7);glyphs.append((b[2]+1,y-6));runs.append(['sup','1',0])
   found=[s for q in doc[0].get_text('dict')['blocks']for ln in q.get('lines',[])for s in ln['spans']if s['text']=='1'and abs(s['origin'][0]-(b[2]+1))<.01and abs(s['origin'][1]-(y-6))<.01];assert len(found)==1
   g=found[0]['bbox'];b=(b[0],min(b[1],g[1]),max(b[2],g[2]),max(b[3],g[3]))
  return b,runs
 if case=='display_heading':
  title='Independently printed display heading';b,runs=printed(title,430,True);element=assemble.Element('h',runs=runs,pno=0,level=2,bbox=b,line_boxes=[b]);element.display_lines=({'runs':copy.deepcopy(runs),'scale':1.0,'bold':None},);expected=[title]
 else:
  b,first=printed(FIRST,430,case in ('first','repeated'));c,last=printed(LAST,458,case in ('last','repeated'));runs=copy.deepcopy(first)+[['t',' ']]+copy.deepcopy(last);element=assemble.Element('list',runs=runs,pno=0,bbox=(b[0],b[1],max(b[2],c[2]),c[3]),line_boxes=[b,c],list_items=[first,last],list_item_boxes=[[b],[c]]);expected=[FIRST,LAST]
 elements.append(element);b=row(HEALTHY,530);elements.append(assemble.Element('p',runs=[['t',HEALTHY]],pno=0,bbox=b,line_boxes=[b]));nb=row('1 The printed native note remains independently owned.',770,10)
 note=assemble.Note(1,'The printed native note remains independently owned.',0,bbox=nb);book=assemble.Book(elements=elements,pages={0:elements},notes=[note],style=skeleton.BookStyle(body_size=12,page_count=1));before=copy.deepcopy(book)
 source=tmp_path/'synthetic-original.pdf';doc.save(source);assert vp.clean_native(doc[0]) and len(glyphs)==(2 if case=='repeated'else 1)
 class Offline:
  cache=tmp_path/'offline';calls=0
  def read(self,*args,**kwargs):self.calls+=1;raise AssertionError('No API in native fixture')
  def read_marker(self,*args,**kwargs):self.calls+=1;raise AssertionError('No marker API in native fixture')
 reader=Offline();reader.cache.mkdir();out=tmp_path/'actual.epub'
 try:
  report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book));assert book==before and reader.calls==0
  assert build_epub.validate(out)==[] and report['degraded_unit_count']==0,'An original printed list/heading note is healthy and cannot abort its page through discarded renderer IDs'
  with zipfile.ZipFile(out)as z:docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
  refs=[(home,n)for home,root in docs.items()for n in root.iter()if tag(n)=='a'and'noteref'in n.get('class','').split()];assert len(refs)==len(glyphs),'Each distinct original raised atom emits exactly one actual note reference'
  ids={}
  for home,root in docs.items():
   for n in root.iter():
    if n.get('id'):ids.setdefault(n.get('id'),[]).append((home,n))
  assert len({ref.get('id')for home,ref in refs})==len(refs)
  for home,ref in refs:
   assert plain(ref)=='1';target=ref.get('href','').split('#')[-1];assert len(ids.get(target,[]))==1
   targethome,note=ids[target][0];assert 'printed native note' in plain(note)
   assert any(tag(n) in ('li','h2')and any(child is ref for child in n.iter())for n in docs[home].iter()),'Every actual reference stays in its own structural source unit'
  first_origin=refs[0][1].get('id');backs=[(home,n)for home,root in docs.items()for n in root.iter()if tag(n)=='a'and n.get('href','').split('#')[-1]==first_origin]
  assert len(backs)==1,'Printed note renders exactly one back-link to its first actual emitted origin, never a discarded rendering'
  back=backs[0][1]
  assert back.get('class')=='note-return'
  assert back.get('aria-label')=='Return to note reference'
  assert ''.join(back.itertext())=='1','Back-link shows exactly the printed note number, without invented words'
  assert sum(tag(n)=='a' and n.get('class')=='note-return' for root in docs.values() for n in root.iter())==1
  assert all(len(ids.get(n.get('href','').split('#')[-1],[]))==1 for home,root in docs.items()for n in root.iter()if tag(n)=='a'and'fnref_'in n.get('href','')),'All native note origin/back destinations are actually published'
  live=' '.join(plain(n)for home,root in docs.items()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',home)for n in root.iter()if tag(n)in ('li','p','h2'))
  assert all(live.count(t)==1 for t in expected+[HEALTHY]),'Structural source words and healthy neighbor remain live once'
 finally:doc.close()
