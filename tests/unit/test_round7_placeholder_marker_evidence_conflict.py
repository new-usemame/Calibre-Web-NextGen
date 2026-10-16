"""SOURCE-free semantic conflict controls; preserve existing frozen15 unchanged."""
import copy,importlib.util
from pathlib import Path
import pytest
s=importlib.util.spec_from_file_location('frozen_placeholder15',Path(__file__).with_name('test_source_placeholder_display_marker_v2.py'));h=importlib.util.module_from_spec(s);s.loader.exec_module(h)

@pytest.mark.parametrize('verified',[True,False])
def test_character_candidate_conflicting_with_original_optical_marker_requires_verification_or_disclosure(verified,tmp_path,monkeypatch):
 prepared=list(h.fixture('3','33'));original_candidates=copy.deepcopy(prepared[3]);assert len(original_candidates)==1 and original_candidates[0]['label']=='3'
 # ORIGINAL ink and original word observation remain3. Independent character
 # observation is9 at the very same source rectangle, a real recognition conflict.
 prepared[3][0]['label']='9'
 response={'label':'3','uncertain':False}if verified else {'label':'','uncertain':True}
 report,docs,images,observed=h.convert(tuple(prepared),tmp_path,monkeypatch,marker_response=response)
 refs=h.assert_owner_and_body(docs)
 if verified:
  assert ''.join(refs[0][1].itertext())=='3','Conflicting character9 versus optical3 needs bounded SOURCE verification, not confident9 substitution'
  receipts=report['pages'][0].get('marker_readings',[])
  assert any(r.get('label')=='3' and r.get('uncertain')is False for r in receipts),'Changed conflicting display must retain its actual verification receipt'
 else:
  h.assert_visible_source_uncertainty(report,docs,images)


def test_missing_marker_bounded_reader_operation_error_keeps_disclosed_BODY_and_owner(tmp_path,monkeypatch):
 prepared=h.fixture('3','33','unobserved')
 # A real Mapping fault occurs when the public synthetic Reader.read_marker
 # operation constructs its returned receipt; no wholehelper/guard is patched.
 from collections.abc import Mapping
 class Unavailable(Mapping):
  def __iter__(self):raise ValueError('synthetic bounded original marker reader unavailable')
  def __len__(self):return 2
  def __getitem__(self,key):raise ValueError('synthetic bounded original marker reader unavailable')
 report,docs,images,observed=h.convert(prepared,tmp_path,monkeypatch,marker_response=Unavailable())
 h.assert_owner_and_body(docs);h.assert_visible_source_uncertainty(report,docs,images)
 assert any('unavailable'in r.get('reason','')for r in report['pages'][0].get('marker_readings',[]))


@pytest.mark.parametrize('operation',['ink_bounds','roi_jpeg'])
def test_new_original_slot_source_operations_have_unit_or_disclosure_containment(operation,tmp_path,monkeypatch):
 import inspect
 from cps.services.reflow.source_display import SourceDisplay
 prepared=h.fixture('3','33','unobserved');reached=[]
 def is_slot_call():return any(f.function=='_placeholder_display_evidence'for f in inspect.stack())
 if operation=='ink_bounds':
  original=h.vision_pixels.ink_bounds
  def controlled(display,*args,**kwargs):
   if is_slot_call():reached.append(operation);raise ValueError('synthetic original slot ink operation unavailable')
   return original(display,*args,**kwargs)
  monkeypatch.setattr(h.vision_pixels,'ink_bounds',controlled)
 else:
  original=SourceDisplay.roi_jpeg
  def controlled(display,*args,**kwargs):
   if is_slot_call()and kwargs.get('scale')==12:reached.append(operation);raise ValueError('synthetic original slot raster operation unavailable')
   return original(display,*args,**kwargs)
  monkeypatch.setattr(SourceDisplay,'roi_jpeg',controlled)
 report,docs,images,observed=h.convert(prepared,tmp_path,monkeypatch)
 assert reached==[operation],'Inject actual new source operation once inside the guarded compilation path'
 if operation=='roi_jpeg':
  h.assert_owner_and_body(docs);h.assert_visible_source_uncertainty(report,docs,images)
 else:
  assert report['degraded_units'] and all(case['page']==0 for case in report['degraded_units'])
  assert any(case.get('stage')=='page_compilation'and 'slot ink operation unavailable'in case.get('reason','')for case in report['degraded_units'])
  assert h.NOTE in ' '.join(''.join(root.itertext())for root in docs.values())
  controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.h.NS)if a.get('class')=='source-disclosure-ref'];assert controls
  for name,a in controls:
   target_file,target=h.h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
   assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.h.NS))
   assert any(h.h.destination(target_file,b.get('href'))==(name,a.get('id'))for b in nodes[0].findall('.//x:a',h.h.NS)if b.get('href')and '#'in b.get('href'))
