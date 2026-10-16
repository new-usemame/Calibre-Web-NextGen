"""Source-free native paragraph projection must retain semantic notes and boundaries."""
import copy,re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,skeleton,vision_pipeline as vp,build_epub

def tag(e):return e.tag.rsplit('}',1)[-1]
def plain(e):return ' '.join(''.join(e.itertext()).split())
def union(boxes):return(min(q[0]for q in boxes),min(q[1]for q in boxes),max(q[2]for q in boxes),max(q[3]for q in boxes))

def fixture(tmp_path,barrier=None):
 doc=pymupdf.open();page=doc.new_page(width=600,height=800);elements=[]
 def ink(text,x,y,size=12):
  page.insert_text((x,y),text,fontsize=size)
  spans=[s for b in page.get_text('dict')['blocks']for l in b.get('lines',[])for s in l['spans']if s['text']==text and abs(s['origin'][0]-x)<.1 and abs(s['origin'][1]-y)<.1];assert len(spans)==1
  return tuple(spans[0]['bbox'])
 for i in range(6):
  t='Calibration source row '+str(i)+' is independently complete.';b=ink(t,70,100+i*24);elements.append(assemble.Element('p',runs=[['t',t]],pno=0,bbox=b,line_boxes=[b]))
 first='The observer describes the';leftbox=ink(first,70,310);left=assemble.Element('p',runs=[['t',first]],pno=0,bbox=leftbox,line_boxes=[leftbox])
 x=94 if barrier=='indent'else 70
 def head(x,y):
  a=ink('1',x,y);rx=a[2];b=ink('st',rx,y-4,7);c=ink(' position and the following evidence',b[2],y);return union([a,b,c])
 b=head(x,330);tail='before closing this account.'if barrier=='terminal'else'before closing this account';c=ink(tail,70,350);m=ink('1',c[2]+1,344,7);end=None if barrier=='terminal'else ink('.',m[2],350)
 tailruns=[['t',' '+tail],['sup','2'if barrier=='mismatch'else'1',0]]+([]if end is None else[['t','.']])
 rr=[['t','1'],['raised','st'],['t',' position and the following evidence']]+tailruns
 cb=union([c,m]+([]if end is None else[end]));right=assemble.Element('h'if barrier=='heading'else'p',runs=rr,pno=0,bbox=union([b,cb]),line_boxes=[b,cb],column=0,level=2 if barrier=='heading'else 0)
 if barrier=='duplicate':
  head(70,450);d=ink(tail,70,470);g=ink('1',d[2]+1,464,7);ink('.',g[2],470)
 note_text='The independently printed note explains this evidence.';nb=ink('1 '+note_text,70,730,10);note=assemble.Note(1,note_text,0,bbox=nb)
 # A terminal sentence with trailing note must not merge into the following paragraph.
 last=assemble.Element('p',runs=[['t','The independently authored next paragraph remains separate.']],pno=0,bbox=ink('The independently authored next paragraph remains separate.',70,380),line_boxes=[])
 last.line_boxes=[last.bbox]
 elements.extend([left,right,last]);book=assemble.Book(elements=elements,pages={0:elements},notes=[note],style=skeleton.BookStyle(body_size=12,page_count=1));physical=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
 doc.save(tmp_path/'synthetic-original.pdf');assert vp.clean_native(page)
 return doc,book,physical,left,right,last

def test_direct_source_proved_ordinal_continuation_retains_note_atom(tmp_path):
 doc,book,physical,left,right,last=fixture(tmp_path)
 try:
  original=copy.deepcopy(left.runs)+[['t',' ']]+copy.deepcopy(right.runs);vp._native_inline_paragraph_ownership(doc,book,0,physical)
  assert len(book.pages[0])==8,'Two fragments of the same original paragraph must become one, preserving the later complete paragraph'
  joined=book.pages[0][6];assert joined.runs==original,'Original raised ordinal and Source-bound note atom must retain identity/order exactly once'
  assert joined.kind=='p'and joined.line_boxes==left.line_boxes+right.line_boxes
  assert book.pages[0][7].text==last.text
 finally:doc.close()

def test_public_source_proved_ordinal_note_continuation_has_one_parent_and_return(tmp_path):
 doc,book,physical,left,right,last=fixture(tmp_path);before=copy.deepcopy(book)
 class Offline:
  cache=tmp_path/'offline';calls=0
  def read(self,*a,**k):self.calls+=1;raise AssertionError('No model dispatch in native fixture')
  def read_marker(self,*a,**k):self.calls+=1;raise AssertionError('No marker dispatch in native fixture')
 reader=Offline();reader.cache.mkdir();out=tmp_path/'actual.epub'
 try:
  report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book));assert reader.calls==0 and book==before and build_epub.validate(out)==[]
  assert report['degraded_unit_count']==0,'Complete unambiguous native Source paragraph/note remains live'
  with zipfile.ZipFile(out)as z:docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
  refs=[(home,e)for home,root in docs.items()for e in root.iter()if tag(e)=='a'and'noteref'in e.get('class','').split()];assert len(refs)==1 and plain(refs[0][1])=='1'
  home,ref=refs[0];owners=[p for p in docs[home].iter()if tag(p)=='p'and any(e is ref for e in p.iter())]
  assert len(owners)==1 and plain(owners[0]).startswith(left.text+' 1st '),'The complete Source-proved sentence and its note origin need one published paragraph parent'
  carriers=[e for e in owners[0].iter()if e.get('data-source-block')];assert carriers,'A semantic note cannot erase the parent Source block ownership'
  target=ref.get('href').split('#')[-1];targets=[e for root in docs.values()for e in root.iter()if e.get('id')==target];assert len(targets)==1
  assert any(tag(e)=='a'and e.get('href','').split('#')[-1]==ref.get('id')for e in targets[0].iter()),'The actual note keeps its exact own Return'
  assert sum(tag(e)=='sup'and plain(e)=='st'for e in owners[0].iter())==1,'Raised ordinal remains raised once'
 finally:doc.close()

@pytest.mark.parametrize('barrier',['indent','heading','mismatch','duplicate'])
def test_semantic_note_does_not_waive_true_or_unproved_source_owner(tmp_path,barrier):
 doc,book,physical,left,right,last=fixture(tmp_path,barrier)
 try:
  before=copy.deepcopy(book.pages[0]);vp._native_inline_paragraph_ownership(doc,book,0,physical)
  assert len(book.pages[0])==len(before),'True Source indent/heading or unproved glyph/ambiguous printed owner remains a paragraph barrier'
  assert book.pages[0]==before,'Source words, semantic atoms and neighboring paragraph remain unchanged when proof fails'
 finally:doc.close()

def test_terminal_full_stop_before_own_note_retains_following_paragraph_boundary(tmp_path):
 doc,book,physical,left,right,last=fixture(tmp_path,'terminal')
 try:
  vp._native_inline_paragraph_ownership(doc,book,0,physical)
  assert book.pages[0][-1].text==last.text and not any(last.text in e.text and 'closing this account' in e.text for e in book.pages[0]),'A genuine full stop before the Source-bound note remains terminal; note projection cannot join the next authored paragraph'
 finally:doc.close()
