"""A bounded reading must not contradict independently proved whole-group ink."""
import importlib.util
from pathlib import Path
import pytest
s=importlib.util.spec_from_file_location('frozen_whole_marker_slot_fixture',Path(__file__).with_name('test_round7_complete_original_marker_slot.py'));f=importlib.util.module_from_spec(s);s.loader.exec_module(f)
@pytest.mark.parametrize('original,candidate,partial,verified',[('13','3',True,'3'),('33','*',False,'*')])
def test_bounded_marker_reading_cannot_collapse_proved_two_component_original_group(original,candidate,partial,verified,tmp_path,monkeypatch):
 prepared,whole=f.damaged(original,candidate,partial)
 report,docs,images,label,calls=f.run(prepared,tmp_path,monkeypatch,{'label':verified,'uncertain':False,'request_dispatched':False})
 assert len(calls)==1,'Complete original raised slot must get at most one independent bounded verification'
 assert not any(r.get('status')=='PROVED'for r in report.get('note_marker_repairs',[])),'Bounded character reading cannot override contradictory original two-component ink cardinality'
 f.h.assert_visible_source_uncertainty(report,docs,images)
 readings=report['pages'][0].get('marker_readings',[]);assert len(readings)==1
 assert readings[0]['label']==verified,'Conflicting actual receipt is retained honestly, not edited to fit known note target'
 b=readings[0]['source_bbox'];assert b[0]<=whole[0]and b[2]>=whole[2],'Contradictory receipt still belongs to complete original group'
