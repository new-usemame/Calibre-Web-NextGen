"""Synthetic public cached-native numbered-list source ownership contract."""
import copy,json,re,zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,skeleton,vision_pipeline as vp,build_epub
FIRST='1. The initial independent numbered entry is complete.'
NEXT='3. The following independent numbered entry is complete.'
TAIL='Healthy closing narrative remains independently authored.'
def tag(n):return n.tag.rsplit('}',1)[-1]
def plain(n):return ' '.join(''.join(n.itertext()).split())
def box(rows):return (min(r['bbox'][0] for r in rows),min(r['bbox'][1] for r in rows),max(r['bbox'][2] for r in rows),max(r['bbox'][3] for r in rows))
def publish(tmp_path,case):
 doc=pymupdf.open();pages={};elements=[];rows=[];figures=[];notes=[]
 def row(pno,text,y,x=70,size=12,bold=False):
  page=doc[pno];page.insert_text((x,y),text,fontsize=size,fontname='hebo' if bold else 'helv')
  matches=[s for b in page.get_text('dict')['blocks'] for ln in b.get('lines',[]) for s in ln['spans'] if s['text']==text];assert len(matches)==1
  r=dict(pno=pno,text=text,bbox=tuple(matches[0]['bbox']),size=size,bold=bold);rows.append(r);return r
 def el(pno,rs,kind='p',column=0,items=None):
  value=' '.join(r['text'] for r in rs);e=assemble.Element(kind,runs=[['t',value]],pno=pno,bbox=box(rs),line_boxes=[r['bbox'] for r in rs],column=column,level=2 if kind=='h' else 0)
  if items:e.list_items=[[['t',' '.join(r['text'] for r in group)]] for group in items];e.list_item_boxes=[[r['bbox'] for r in group] for group in items]
  pages[pno].append(e);elements.append(e);return e
 for pno in range(2):
  doc.new_page(width=600,height=800);pages[pno]=[]
  for i in range(6):
   calibration='Calibration page '+str(pno)+' row '+str(i)+' is separately complete.'
   if case=='hyphenated' and pno==0 and i==0:calibration='Calibration international is independently printed as one whole word.'
   el(pno,[row(pno,calibration,200+i*24)])
 left='2. The continuing numbered entry requires the'
 suffix='remaining source phrase to finish its explanation.'
 if case=='hyphenated':left='2. The continuing numbered entry concerns inter-';suffix='national measurements in the same explanation.'
 if case=='literal_hyphen':left='2. The continuing numbered entry preserves semi-';suffix='formal usage in the same explanation.'
 if case=='terminal':left='2. The second numbered entry is independently complete.';suffix='This begins a new independently indented account.'
 first=row(0,FIRST,710);last=row(0,left,746);el(0,[first,last],kind='list',items=[[first],[last]])
 if case=='note':
  foot=row(0,'1 This synthetic footer retains its own supporting note.',782,x=85,size=10);notes=[assemble.Note(1,'This synthetic footer retains its own supporting note.',0,bbox=foot['bbox'])]
 y=90 if case!='figure' else 140
 if case=='figure':
  b=(100.,30.,500.,100.);doc[1].draw_rect(b,color=(0,0,0),width=1);doc[1].draw_line((100,30),(500,100),color=(0,0,0),width=1)
  e=assemble.Element('fig',pno=1,bbox=b,placed='geometry');pages[1].insert(0,e);elements.insert(len(elements)-6,e);figures=[dict(pno=1,bbox=b,found='native')]
 if case=='heading':
  right=row(1,'Separate authored heading',y,size=16,bold=True);e=el(1,[right],kind='h');pages[1].remove(e);pages[1].insert(0,e)
  nextrow=row(1,NEXT,y+40);e=el(1,[nextrow],kind='list',items=[[nextrow]]);pages[1].remove(e);pages[1].insert(1,e)
 elif case=='fresh':
  nextrow=row(1,NEXT,y);e=el(1,[nextrow],kind='list',items=[[nextrow]]);pages[1].remove(e);pages[1].insert(0,e)
 else:
  x=280 if case=='gutter' else 94 if case=='terminal' else 88
  right=row(1,suffix,y,x=x,size=12);nextrow=row(1,NEXT,y+32,x=280 if case=='gutter' else 70,size=12)
  # Cached source owner joins suffix and next numbered item as one p: repair
  # must use original physical rows, not trust this erroneous grouping.
  e=el(1,[right,nextrow],column=1 if case=='gutter' else 0);pages[1].remove(e);pages[1].insert(0 if case!='figure' else 1,e)
 end=row(1,TAIL,430);el(1,[end]);elements=[e for pno in range(2) for e in pages[pno]]
 book=assemble.Book(elements=elements,pages=pages,notes=notes,figures=figures,style=skeleton.BookStyle(body_size=12,page_count=2))
 before=copy.deepcopy(book);doc.save(tmp_path/'synthetic-original.pdf');assert all(vp.clean_native(doc[p]) for p in range(2))
 (tmp_path/'synthetic-source-proof.json').write_text(json.dumps(dict(case=case,rows=rows,elements=[dict(kind=e.kind,pno=e.pno,runs=e.runs,bbox=e.bbox,line_boxes=e.line_boxes,column=e.column,list_items=e.list_items,list_item_boxes=e.list_item_boxes) for e in elements],figures=figures),indent=2))
 class Offline:
  cache=tmp_path/'offline';dispatches=0
  def read(self,*a,**k):self.dispatches+=1;raise AssertionError('Native control must never dispatch')
  def read_marker(self,*a,**k):self.dispatches+=1;raise AssertionError('Native control must never dispatch marker')
 reader=Offline();reader.cache.mkdir();out=tmp_path/'actual.epub'
 try:
  report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book));assert reader.dispatches==0 and book==before
  assert build_epub.validate(out)==[] and report['native_pages']==[0,1] and report['vision_pages']==[]
  with zipfile.ZipFile(out) as z:roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
  reading=[];lists=[];art=[];asides=[]
  def visit(n,inside=False):
   inside=inside or tag(n)=='aside'
   if tag(n)=='aside':asides.append(n)
   if not inside and tag(n)=='li':lists.append(n)
   if not inside and tag(n)=='figure':art.append(n)
   if not inside and tag(n) in ('li','p','h1','h2','h3') and not any(tag(c) in ('li','p','h1','h2','h3') for c in n.iter() if c is not n):reading.append(n)
   for c in n:visit(c,inside)
  for root in roots:visit(root)
  visible=' '.join(plain(n) for n in reading)
  assert visible.count(FIRST)==1 and visible.count(NEXT)==1 and visible.count(TAIL)==1,'Healthy entries and neighbor prose survive exactly once'
  (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2))
  return dict(roots=roots,reading=reading,lists=lists,art=art,asides=asides,report=report,left=left,suffix=suffix,visible=visible)
 finally:doc.close()
@pytest.mark.parametrize('case',['plain','hyphenated','literal_hyphen','figure','note'])
def test_continued_numbered_item_owns_only_its_suffix_and_next_item_is_separate_li(tmp_path,case):
 r=publish(tmp_path,case);joined=r['left']+' '+r['suffix']
 if case=='hyphenated':joined=r['left'][:-1]+r['suffix']
 if case=='literal_hyphen':joined=r['left']+r['suffix']
 assert sum(plain(n)==joined for n in r['lists'])==1,'Original continued item must become one list item with its own suffix only'
 assert sum(plain(n)==NEXT for n in r['lists'])==1,'Source-proved next numbered item cannot be inside preceding item or ordinary p'
 assert not any(NEXT in plain(n) and r['suffix'] in plain(n) for n in r['reading']),'Distinct source item identities must not fuse'
 if case=='figure':
  assert len(r['art'])==1,'Original figure retained once'
  ordered=[n for n in r['roots'][0].iter() if n in r['lists'] or n in r['art']]
  assert ordered.index(next(n for n in ordered if tag(n)=='figure'))>ordered.index(next(n for n in ordered if tag(n)=='li' and plain(n)==joined)),'Figure source boundary must not interrupt the continued list item'
 if case=='note':assert any('synthetic footer retains' in plain(n) for n in r['asides']),'Footer note remains separately source owned'
@pytest.mark.parametrize('case',['fresh','heading','terminal','gutter'])
def test_fresh_list_heading_terminal_boundary_and_foreign_column_not_swallowed(tmp_path,case):
 r=publish(tmp_path,case)
 assert any(plain(n)==r['left'] for n in r['lists']),'Independent left item is retained exactly'
 assert not any(r['left'] in plain(n) and (r['suffix'] in plain(n) or NEXT in plain(n)) for n in r['reading']),'Unproved continuation must not consume a new list, heading, paragraph or column'
 if case=='heading':assert any(tag(n).startswith('h') and plain(n)=='Separate authored heading' for n in r['reading'])
 if case=='fresh':assert any(plain(n)==NEXT for n in r['lists'])
