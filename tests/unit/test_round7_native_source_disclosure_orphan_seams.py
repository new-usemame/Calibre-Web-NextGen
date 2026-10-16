"""SOURCE-free synthetic native sentence/control boundaries, frozen before C24.
The orphan admission is deliberately narrower than general indentation: exact
native foot/top endpoints, an unfinished comma, a lone lowercase printed row.
"""
import copy,re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,skeleton,vision_pipeline as vp
NS={'x':'http://www.w3.org/1999/xhtml'}
LEFT_A='The amber observers carried their original records'
LEFT_B='toward the ordinary bridge, and waited,'
LEFT=LEFT_A+' '+LEFT_B
RIGHT='while the silver workers arrived.'
LATER='A separate violet conclusion remains complete.'
def plain(node):return ' '.join(''.join(node.itertext()).split())
def fixture(tmp,case):
 doc=pymupdf.open();pages={0:[],1:[]}
 def row(n,value,y,x=70,kind='p'):
  page=doc[n];page.insert_text((x,y),value,fontsize=12)
  rows=[ln for b in page.get_text('dict')['blocks']for ln in b.get('lines',[])if ''.join(s['text']for s in ln['spans'])==value];assert len(rows)==1
  return tuple(rows[0]['bbox'])
 def paragraph(n,values,ys,xs=None,kind='p'):
  boxes=[row(n,v,y,(xs or [70]*len(values))[i],kind)for i,(v,y)in enumerate(zip(values,ys))]
  box=(min(b[0]for b in boxes),min(b[1]for b in boxes),max(b[2]for b in boxes),max(b[3]for b in boxes))
  pages[n].append(assemble.Element(kind,pno=n,runs=[['t',' '.join(values)]],bbox=box,line_boxes=boxes,level=2 if kind=='h'else 0))
 for _ in range(2):doc.new_page(width=600,height=800)
 paragraph(0,['A complete preceding paragraph establishes an ordinary margin.'],[70])
 tail=LEFT_B[:-1]+'.' if case=='terminal'else LEFT_B
 paragraph(0,[LEFT_A,tail],[700,720])
 # Original native drawing crosses the actual printed paragraph. A clean crop
 # cannot be proved: the real converter must retain a disclosed whole SOURCE.
 page=doc[0];page.draw_rect((90,650,490,755),width=1)
 for x in (160,250,340,430):page.draw_line((x,650),(x,755),width=1)
 page.draw_line((90,650),(490,755),width=1)
 if case=='heading':paragraph(1,['A Genuine Separate Section'],[40],kind='h')
 head='The silver workers begin a separate account.' if case=='new-paragraph'else RIGHT
 paragraph(1,[head],[70],xs=[70 if case=='unindented'else 90])
 paragraph(1,[LATER],[160])
 book=assemble.Book(elements=sum(pages.values(),[]),pages=pages,style=skeleton.BookStyle(body_size=12,page_count=2))
 return doc,book,LEFT_A+' '+tail,head
class Offline:
 def __init__(self,tmp):self.cache=tmp/'offline';self.cache.mkdir();self.dispatches=0
 def read(self,*a,**k):self.dispatches+=1;raise AssertionError('No model dispatch for original native synthetic fixture')
 def read_marker(self,*a,**k):self.dispatches+=1;raise AssertionError('No marker dispatch for original native synthetic fixture')
@pytest.mark.parametrize('case',['orphan','unindented','new-paragraph','heading','terminal'])
def test_public_native_disclosure_preserves_proved_sentence_and_true_boundaries(tmp_path,case):
 doc,book,left,right=fixture(tmp_path,case);before=copy.deepcopy(book);reader=Offline(tmp_path);path=tmp_path/'actual.epub'
 try:report=vp.convert(doc,str(path),reader,native_prepared=SimpleNamespace(book=book))
 finally:doc.close()
 assert reader.dispatches==0 and book==before
 assert build_epub.validate(path)==[]
 assert report['native_pages']==[0,1] and not report['vision_pages']
 with zipfile.ZipFile(path)as z:
  docs={n:ET.fromstring(z.read(n))for n in z.namelist()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)}
  units=[u for u in report['degraded_units']if u['page']==0 and str(u.get('block','')).startswith('native-vector-')]
  assert len(units)==1,'The real inseparable native vector must exercise a disclosed SOURCE context'
  refs=[(home,a)for home,root in docs.items()for a in root.findall('.//x:a',NS)if a.get('class')=='source-disclosure-ref'and a.get('id')==units[0]['source_reference_origin']]
  assert len(refs)==1
  home,ref=refs[0];dest,ident=ref.get('href').split('#');targethome='OEBPS/'+dest if dest else home;target=next(n for n in docs[targethome].iter()if n.get('id')==ident)
  images=target.findall('.//x:img',NS);assert images and all('OEBPS/'+i.get('src')in z.namelist()for i in images)
  assert any(a.text=='Return'and a.get('href').split('#')[-1]==ref.get('id')and (a.get('href').split('#')[0]or targethome.removeprefix('OEBPS/'))==home.removeprefix('OEBPS/')for a in target.findall('.//x:a',NS))
  pars=[n for root in docs.values()for n in root.findall('.//x:p',NS)if n.get('class','')not in ('caption','source-provenance')]
  if case in ('orphan','unindented'):
   joined=[p for p in pars if plain(p)==left+' '+right]
   assert len(joined)==1,'A proved unfinished native sentence must resume before the generated SOURCE disclosure despite orphan indentation'
   assert not joined[0].findall('.//x:a[@class="source-disclosure-ref"]',NS),'Generated disclosure cannot be nested in the authored sentence'
  else:
   assert sum(plain(p)==left for p in pars)==sum(plain(p)==right for p in pars)==1,'Genuine new paragraph, heading, and terminal sentence must stay separate'
   assert not any(plain(p)==left+' '+right for p in pars)
  assert sum(plain(p)==LATER for p in pars)==1

def endpoint_records():
 left=dict(role='body',native_source=True,native_page_foot=True,printed_tail='waited,',printed_tail_box=[200,708,247,723],source_regions=[[70,687,350,723]],source_rows=[[70,687,350,702],[70,708,247,723]],opening_column_left=70)
 right=dict(role='body',native_source=True,native_page_top=True,printed_head='while',printed_head_box=[90,57,120,73],first_line_x=90,opening_column_left=70,source_regions=[[90,57,280,73]],source_rows=[[90,57,280,73]])
 return left,right
@pytest.mark.parametrize('case',['orphan','wrong-tail','wrong-head','missing-box','not-page-top'])
def test_orphan_admission_requires_exact_original_physical_endpoints(case):
 left,right=endpoint_records()
 if case=='wrong-tail':left['printed_tail']='completed.'
 if case=='wrong-head':right['printed_head']='different'
 if case=='missing-box':right['printed_head_box']=None
 if case=='not-page-top':right['native_page_top']=False
 decision=vp._continuation_decision(left,right,[right],LEFT,RIGHT)
 assert (decision['status']=='joined')==(case=='orphan'),'A lone comma/lowercase native orphan needs both exact painted endpoints and native foot/top; geometry or wording failure cannot earn a join'
