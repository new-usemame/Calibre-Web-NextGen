"""Original seam predicates reached inside public assembly ownership.

Synthetic source only. The real merger executes its original raise expression;
no replacement exception substitutes for it. Publication must recover only the
known two seam fragments and preserve unrelated source prose.
"""
import inspect,zipfile
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible

FIRST='The amber orchard remains an intact healthy paragraph.'
LAST='The emerald instrument remains a separate healthy paragraph.'
LATER='The later indigo page remains live and complete.'
CASES={
 'EXPLICIT-164':'emitted protected raster endpoint differs',
 'EXPLICIT-165':'emitted layout boundary differs from source hyphen',
 'EXPLICIT-166':'unapproved boundary hyphen',
 'EXPLICIT-167':'unsupported continuation role',
}

@pytest.mark.parametrize('site',list(CASES))
def test_original_seam_rejection_completes_only_owned_source_fragments(tmp_path,monkeypatch,site):
 left=h.InkPage();left.text(FIRST,'first',y=45)
 left.text('The patient collector carried the specimen toward','tail',y=350,next=True)
 right=h.InkPage();right.text('the shaded tool station beside the river.','head',y=45,previous=True)
 right.text(LAST,'last',y=180)
 later=h.InkPage();later.text(LATER,'later',y=70)
 original=build_epub._merge_layout_paragraphs;signature=inspect.signature(original);reached=[]
 def inject_original_input(left_fragment,right_fragment,*args,**kwargs):
  if 'patient collector' not in left_fragment:return original(left_fragment,right_fragment,*args,**kwargs)
  assert not reached,'The original owned rejection must not repeat after SOURCE replay'
  assert 'shaded tool station' in right_fragment
  call=signature.bind(left_fragment,right_fragment,*args,**kwargs);call.apply_defaults()
  if site=='EXPLICIT-164':call.arguments.update(pixel_boundary=True,hyphen=None)
  elif site=='EXPLICIT-165':call.arguments.update(hyphen='drop',pixel_boundary=False)
  elif site=='EXPLICIT-166':
   assert left_fragment.endswith('</p>')
   call.arguments.update(left=left_fragment[:-4]+'-</p>',hyphen=None,pixel_boundary=False)
  else:call.arguments.update(role='synthetic_unsupported_role',hyphen=None,pixel_boundary=False)
  try:original(*call.args,**call.kwargs)
  except Exception as error:
   assert type(error).__name__=='ContractError' and str(error)==CASES[site]
   frames=[];tb=error.__traceback__
   while tb:
    if tb.tb_frame.f_code is original.__code__:frames.append({'file':tb.tb_frame.f_code.co_filename,'line':tb.tb_lineno})
    tb=tb.tb_next
   assert len(frames)==1,'Actual original merger raise expression must execute'
   reached.append(dict(site=site,cause_type=type(error).__name__,cause_message=str(error),original_frame=frames[0]));raise
  pytest.fail('Original seam predicate was not reached')
 monkeypatch.setattr(build_epub,'_merge_layout_paragraphs',inject_original_input)
 path,report,docs=h.convert_ink(tmp_path,monkeypatch,[left,right,later])
 assert len(reached)==1 and reached[0]['cause_message']==CASES[site]
 assert build_epub.validate(path)==[] and set(map(int,report['page_homes']))=={0,1,2}
 bodies=h.body_texts(docs);assert bodies.count(FIRST)==bodies.count(LAST)==bodies.count(LATER)==1
 units=report['degraded_units'];assert {(u['page'],u.get('block'))for u in units}=={(0,'tail'),(1,'head')}
 assert all(u['stage']=='assembly_seam' and CASES[site] in u['reason']for u in units)
 assert report['reread_pages']==[],'Offline source-only fixture has no paid route'
 source_visible(path,report,{0,1,2})
 with zipfile.ZipFile(path)as z:
  for unit in units:assert 'OEBPS/'+unit['source_image'] in z.namelist()
  refs=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref']
  for unit in units:
   candidates=[(name,a)for name,a in refs if a.get('href','').split('#')[-1]==unit['source_reference']];assert candidates
   for name,a in candidates:
    home,ident=h.destination(name,a.get('href'));target=next(n for n in docs[home].iter()if n.get('id')==ident)
    assert target.findall('.//x:img',h.NS)
    assert any(back.text=='Return' and h.destination(home,back.get('href'))==(name,a.get('id'))for back in target.findall('.//x:a',h.NS))
