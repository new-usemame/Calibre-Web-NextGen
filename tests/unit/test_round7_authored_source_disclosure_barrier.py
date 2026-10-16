"""A supplemental original vector notice is not an authored-unit replacement.
These SOURCE-free public converter controls protect rejected native paragraph/
heading ownership while preserving healthy neighbors and retained originals.
"""
import copy,re,zipfile
from xml.etree import ElementTree as ET
from types import SimpleNamespace
import pytest
from tests.unit.test_round7_native_source_disclosure_orphan_seams import fixture,Offline,plain,NS
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
REPLACED='because another original receipt remains part of this account,'
@pytest.mark.parametrize('kind',['list','h'])
def test_rejected_authored_unit_remains_reading_flow_barrier(tmp_path,kind):
 doc,book,left,right=fixture(tmp_path,'unindented')
 page=doc[0];page.insert_text((70,770),REPLACED,fontsize=12)
 row=next(ln for b in page.get_text('dict')['blocks']for ln in b.get('lines',[])if ''.join(s['text']for s in ln['spans'])==REPLACED)
 box=tuple(row['bbox']);el=assemble.Element(kind,pno=0,runs=[['t',REPLACED]],bbox=box,line_boxes=[box],level=2 if kind=='h'else 0)
 if kind=='list':el.list_items=[[['t','An unproved synthetic list representation.']]]
 else:el.display_lines=({'runs':[['t','An unproved synthetic heading representation.']],'scale':1,'bold':False},)
 book.pages[0].append(el);book.elements=sum(book.pages.values(),[]);before=copy.deepcopy(book);reader=Offline(tmp_path);out=tmp_path/'actual.epub'
 try:report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
 finally:doc.close()
 assert reader.dispatches==0 and book==before
 assert build_epub.validate(out)==[]
 assert report['native_pages']==[0,1]and not report['vision_pages']
 units=[u for u in report['degraded_units']if u.get('block')=='native-element-2']
 assert len(units)==1 and units[0]['page']==0,'The actual original representation predicate must retain its exact authored unit'
 with zipfile.ZipFile(out)as z:
  roots={n:ET.fromstring(z.read(n))for n in z.namelist()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)}
  refs=[(name,a)for name,root in roots.items()for a in root.findall('.//x:a',NS)if a.get('id')==units[0]['source_reference_origin']]
  assert len(refs)==1
  home,ref=refs[0];dest,ident=ref.get('href').split('#');targethome='OEBPS/'+dest if dest else home
  target=next(n for n in roots[targethome].iter()if n.get('id')==ident)
  assert target.findall('.//x:img',NS)and all('OEBPS/'+i.get('src')in z.namelist()for i in target.findall('.//x:img',NS))
  assert any(a.text=='Return'and a.get('href').split('#')[-1]==ref.get('id')for a in target.findall('.//x:a',NS))
  pars=[p for root in roots.values()for p in root.findall('.//x:p',NS)]
  assert not any(plain(p)==left+' '+right for p in pars),'An authored SOURCE replacement is an opaque reading-flow barrier; never join live text around its omitted wording'
  assert sum(plain(p)==left for p in pars)==sum(plain(p)==right for p in pars)==1,'Both healthy neighbors must stay live, once, in their original order'
  assert not any('An unproved synthetic' in plain(p)for p in pars),'Rejected representation wording cannot reappear as authored prose'
