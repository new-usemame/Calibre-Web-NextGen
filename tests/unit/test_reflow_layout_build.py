"""Actual EPUB publication must compile source plans and preserve their assets."""
import json
import zipfile
import pytest
from cps.services.reflow import build_epub,layout_ops,extract,enriched_source
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_structural_ops import source,texts,X
from tests.unit.test_reflow_layout_ops import answer
pytestmark=pytest.mark.unit

def test_builder_applies_layout_after_canonical_reset_and_packages_protected_assets(source):
 book,doc,tmp=source
 canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
 raw=extract.read_page(doc,0);prepared=layout_ops.prepare(book,doc,0,canonical,raw)
 response=answer(prepared);response['groups'][0]['role']='heading1'
 plan=prepared.accept(book,doc,response,source_page=canonical,raw_page=raw)
 result=build_epub.build(book,str(tmp/'layout.epub'),doc=doc,source_pages={0:canonical},layout_plans=[plan],raw_pages={0:raw})
 assert build_epub.validate(result.path)==[]
 headings=[h for root in texts(result.path) for h in root.iter(X+'h1')]
 assert any(''.join(h.itertext())=='Learning the sky' for h in headings)
 with zipfile.ZipFile(result.path) as archive:
  assert archive.getinfo('OEBPS/images/fig_p0000_0.jpg').file_size>0
  report=json.loads(archive.read('META-INF/reflow.json'))['layout_operations']
  assert report['applied_pages']==[0]
  assert report['pages'][0]['snapshot']==prepared.snapshot_id

def test_changed_raw_cannot_reach_publication(source):
 book,doc,tmp=source;canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
 raw=extract.read_page(doc,0);prepared=layout_ops.prepare(book,doc,0,canonical,raw)
 plan=prepared.accept(book,doc,answer(prepared),source_page=canonical,raw_page=raw)
 raw.blocks[0].lines[0].spans[0].text+=' altered'
 with pytest.raises(ContractError):
  build_epub.build(book,str(tmp/'bad.epub'),doc=doc,source_pages={0:canonical},layout_plans=[plan],raw_pages={0:raw})
 assert not (tmp/'bad.epub').exists()

from tests.unit.test_reflow_layout_ops import boundary_source

@pytest.mark.parametrize('mode,word',[('keep','co-operation'),('drop','cooperation'),(None,'co-')])
def test_model_boundary_uses_explicit_hyphen_and_no_fallback_heuristic(boundary_source,tmp_path,mode,word):
 book,doc,sources,raws=boundary_source;plans=[]
 for pno in range(2):
  context=dict(previous_source=sources[0],previous_raw=raws[0]) if pno else {}
  prepared=layout_ops.prepare(book,doc,pno,sources[pno],raws[pno],**context)
  response=answer(prepared)
  if pno and mode:
   contract=json.loads(prepared.contract_json)
   response.update(continuation=True,boundary_join=dict(left=contract['previous']['wrap_lefts'][-1],right=contract['wrap_rights'][0],hyphen=mode))
  plans.append(prepared.accept(book,doc,response,source_page=sources[pno],raw_page=raws[pno],**context))
 result=build_epub.build(book,str(tmp_path/('join-'+str(mode)+'.epub')),doc=doc,source_pages=dict(enumerate(sources)),layout_plans=plans,raw_pages=dict(enumerate(raws)))
 assert build_epub.validate(result.path)==[]
 paragraphs=[''.join(p.itertext()) for root in texts(result.path) for p in root.iter(X+'p')]
 assert any(word in p for p in paragraphs)
 if mode:assert any(word+' resumes' in p for p in paragraphs)
 else:assert not any('cooperation' in p or 'co-operation' in p for p in paragraphs)
 assert result.page_joins==(1 if mode else 0)


def test_three_page_chain_keeps_source_notices_and_executes_both_admitted_seams():
 pages=[dict(pno=0,body=['<p>An inter-</p>'],asides=[],anchor=''),
        dict(pno=1,body=['<p>national co-</p>','<section class="reflow-retained-furniture"><p>12</p></section>'],asides=[],anchor='<span id="pg_0001"></span>'),
        dict(pno=2,body=['<p>operation continues.</p>'],asides=[],anchor='<span id="pg_0002"></span>')]
 done=set()
 assert build_epub._join_page_turns(pages,layout_pages={0,1,2},layout_boundaries={1:{'hyphen':'drop'},2:{'hyphen':'drop'}},executed=done)==2
 assert done=={1,2}
 from xml.etree import ElementTree as ET
 assert ''.join(ET.fromstring(pages[0]['body'][0]).itertext())=='An international cooperation continues.'
 assert pages[1]['body']==['<section class="reflow-retained-furniture"><p>12</p></section>']


def test_failed_protected_crop_never_publishes_a_successful_layout(source,monkeypatch):
 book,doc,tmp=source;canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
 raw=extract.read_page(doc,0);prepared=layout_ops.prepare(book,doc,0,canonical,raw)
 plan=prepared.accept(book,doc,answer(prepared),source_page=canonical,raw_page=raw)
 crop=extract.crop_jpeg
 def fail_figure(doc,pno,rect,*args,**kwargs):
  if tuple(rect)==(135,235,265,365):raise ValueError('fixture figure crop failure')
  return crop(doc,pno,rect,*args,**kwargs)
 monkeypatch.setattr(extract,'crop_jpeg',fail_figure)
 target=tmp/'missing-asset.epub'
 with pytest.raises(ContractError,match='protected layout resource'):
  build_epub.build(book,str(target),doc=doc,source_pages={0:canonical},layout_plans=[plan],raw_pages={0:raw})
 assert not target.exists()
