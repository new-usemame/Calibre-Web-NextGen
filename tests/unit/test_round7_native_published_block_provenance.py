"""Actual native EPUB provenance feeds the immutable DOM excerpt method.

All printed words are synthetic; no alternate scorer, source facts or requests.
"""
from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,zipfile,hashlib
from lxml import etree as E
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
METHOD=Path(__file__).resolve().parents[1]/'fixtures/reflow_dom_accuracy.py'
spec=importlib.util.spec_from_file_location('round7_frozen_native_dom_accuracy',METHOD);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert hashlib.sha256(METHOD.read_bytes()).hexdigest()=='0b2f74983d2d93e03c5a5d083539f6de3b9358775d0a663d367feb194c19021a'
TEXT='The amber notebook preserves a complete independently printed native source sentence.'
LEFT='The native observers explain that the amber specimen is'
RIGHT='approaching the ordinary harbor beside the quiet river.'
LATER='A separate later paragraph retains its independent source identity and complete sentence.'


def native(tmp_path,kind='p',unbound=False,seam=False,degraded=False):
 doc=pymupdf.open();pages={};truth=[]
 inputs=[[(LEFT,'p',570)],[(RIGHT,'p',85),(LATER,'p',170)]] if seam else [[(TEXT,kind,160),(LATER,'p',270)]]
 for pno,rows in enumerate(inputs):
  page=doc.new_page(width=440,height=600);elements=[]
  for text,k,y in rows:
   page.insert_text((40,y),text,fontsize=8)
   published='A separate unbound model paragraph has no exact source occurrence.' if unbound and text==TEXT else text
   elements.append(assemble.Element(k,runs=[['t',published]],level=2 if k=='h' else 0,pno=pno,bbox=None if unbound and text==TEXT else [39,y-11,410,y+4]))
   truth.append(dict(id='synthetic-'+str(pno)+'-'+str(len(elements)-1),book=1,page_index0=pno,bbox=[39,y-11,410,y+4],ground_truth=text))
  pages[pno]=elements
 book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
 class Reader:
  cache=tmp_path/'unused-cache'
  def read(self,*a):raise AssertionError('No native model dispatch')
 Reader.cache.mkdir();out=tmp_path/'native.epub'
 try:report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
 finally:doc.close()
 assert build_epub.validate(out)==[]
 assert report['vision_pages']==[] and report['native_pages']==([] if degraded else list(pages))
 return out,report,truth


def mutate(path,tmp_path,action):
 out=tmp_path/(action+'.epub')
 with zipfile.ZipFile(path) as z,zipfile.ZipFile(out,'w') as w:
  for name in z.namelist():
   data=z.read(name)
   if name.startswith('OEBPS/ch') and name.endswith('.xhtml'):
    root=E.fromstring(data)
    for e in root.xpath('//*[@data-source-page="0"]'):
     if TEXT in ''.join(e.itertext()):
      if action=='hidden':e.set('style','display:none')
      elif action=='deleted':
       for c in list(e):e.remove(c)
       e.text=''
      elif action=='geometry':e.set('data-source-regions','[[1,1,3,3]]')
    data=E.tostring(root,xml_declaration=True,encoding='utf-8')
   w.writestr(name,data)
 return out


@pytest.mark.parametrize('kind',['p','h','caption'])
def test_bound_native_body_heading_caption_have_actual_matching_block_provenance(tmp_path,kind):
 out,report,truth=native(tmp_path,kind);dom=m.extract_actual(out)
 for sample in truth:
  result=m.measure_span(sample,sample['ground_truth'],dom)
  assert result['errors']==0 and result['candidate_words']>0,'actual visible native SOURCE span must reach frozen measure_span'
  assert result['selected_block_ids'] and not result['missing_live_dom_blocks']
  assert not result['dom_sidecar_geometry_mismatches']
  for bid in result['selected_block_ids']:
   block=next(b for b in report['pages'][sample['page_index0']]['blocks'] if b['id']==bid)
   assert dom['geometries'][(sample['page_index0'],bid)]==block['source_regions']


def test_native_join_preserves_both_original_page_block_keys_through_chapters(tmp_path):
 out,report,truth=native(tmp_path,seam=True);dom=m.extract_actual(out)
 for sample in truth:
  result=m.measure_span(sample,sample['ground_truth'],dom)
  assert result['errors']==0 and result['candidate_words']>0
  assert not result['missing_live_dom_blocks'] and not result['dom_sidecar_geometry_mismatches']
 with zipfile.ZipFile(out) as z:
  paragraphs=[e for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml') for e in E.fromstring(z.read(n)).xpath('//*[local-name()="p"]')]
 joined=[p for p in paragraphs if LEFT in ''.join(p.itertext()) and RIGHT in ''.join(p.itertext())]
 assert len(joined)==1
 assert {s.get('data-source-page') for s in joined[0].xpath('.//*[@data-source-block]')}=={'0','1'}


def test_geometry_mismatch_is_reported_by_frozen_method_not_silently_admitted(tmp_path):
 out,_,truth=native(tmp_path);dom=m.extract_actual(mutate(out,tmp_path,'geometry'));result=m.measure_span(truth[0],TEXT,dom)
 assert result['dom_sidecar_geometry_mismatches'],'native publication provenance must expose deliberately wrong source geometry'


@pytest.mark.parametrize('action',['hidden','deleted'])
def test_hidden_or_deleted_native_text_has_zero_live_excerpt_credit(tmp_path,action):
 out,_,truth=native(tmp_path);dom=m.extract_actual(mutate(out,tmp_path,action));result=m.measure_span(truth[0],TEXT,dom)
 assert result['candidate_words']==0 and result['deletions']==result['reference_words']


def test_unbound_native_element_cannot_invent_source_block_provenance(tmp_path):
 out,report,truth=native(tmp_path,unbound=True);dom=m.extract_actual(out);result=m.measure_span(truth[0],TEXT,dom)
 assert result['candidate_words']==0
 unbound=[b for b in report['pages'][0]['blocks'] if not b['source_regions']]
 assert unbound and all((0,b['id']) not in dom['blocks'] for b in unbound)


def test_source_image_only_page_has_no_live_native_excerpt_credit(tmp_path,monkeypatch):
 original=vp._native_seam_blocks;reached=[]
 def reject(doc,book,pno,page):
  if pno==0:reached.append(pno);raise ValueError('Synthetic native SOURCE binding unit unavailable')
  return original(doc,book,pno,page)
 monkeypatch.setattr(vp,'_native_seam_blocks',reject)
 out,report,truth=native(tmp_path,degraded=True);dom=m.extract_actual(out);result=m.measure_span(truth[0],TEXT,dom)
 assert reached and report['degraded_units']
 assert result['candidate_words']==0 and result['deletions']==result['reference_words']
 assert dom['counts'].get('body_lexical_words',0)==0
