"""Independent conflicting optical observations for one original marker slot."""
import copy,importlib.util
from pathlib import Path
import pytest
s=importlib.util.spec_from_file_location('frozen_placeholder15',Path(__file__).with_name('test_source_placeholder_display_marker_v2.py'));h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
@pytest.mark.parametrize('verified',[True,False])
def test_one_character_candidate_matching_one_of_two_conflicting_optical_observations_is_not_unique_source_proof(verified,tmp_path,monkeypatch):
 prepared=list(h.fixture('3','33'));markers=[w for w in prepared[1]if w['text']=='3'and w['bbox'][1]<80]
 assert len(markers)==1,'Original optical marker instance must be independently observed at the original raised glyph'
 duplicate=copy.deepcopy(markers[0]);duplicate['text']='9';prepared[1].append(duplicate);prepared[3][0]['label']='9'
 response={'label':'3','uncertain':False}if verified else {'label':'','uncertain':True}
 report,docs,images,observed=h.convert(tuple(prepared),tmp_path,monkeypatch,marker_response=response);refs=h.assert_owner_and_body(docs)
 if verified:
  assert ''.join(refs[0][1].itertext())=='3','Conflicting overlapping original optical observations require bounded verification, not agreement with only one observation'
  assert any(r.get('label')=='3'and r.get('uncertain')is False for r in report['pages'][0].get('marker_readings',[]))
 else:h.assert_visible_source_uncertainty(report,docs,images)
