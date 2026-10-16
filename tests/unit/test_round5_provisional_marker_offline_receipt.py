"""SOURCE-free provisional marker cache-miss publication bookkeeping."""
import importlib.util
from pathlib import Path

import pytest
from cps.services.reflow import vision_pipeline, vision_pixels

_helper=Path(__file__).with_name('test_round5_provisional_adjacent_note_source_slot.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round5_provisional_adjacent_note_source_slot.py'
_spec=importlib.util.spec_from_file_location('round5_offline_marker_source_slot',_helper)
s=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(s)
pytestmark=pytest.mark.unit


def test_unverified_provisional_offline_slot_publishes_zero_cost_deferred_receipt(tmp_path,monkeypatch):
    prepared=s.fixture()
    original_convert=vision_pipeline.convert
    calls=[]
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda *args,**kwargs:[])
    def convert(doc,path,reader,*args,**kwargs):
        def offline(image):
            calls.append(image)
            raise vision_pipeline.vision_reading.model.ModelError('Printed marker absent from offline cache')
        reader.read_marker=offline
        return original_convert(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    _,report,docs=s.h.convert_ink(tmp_path,monkeypatch,[prepared[0]])
    assert len(calls)==1
    refs,_=s.h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())=='‡'
    assert s.h.body_texts(docs)==[prepared[1]+'‡ '+prepared[2]]
    page=report['pages'][0]
    assert len(page['marker_readings'])==1
    receipt=page['marker_readings'][0]
    assert receipt['deferred'] is True and receipt['request_dispatched'] is False
    assert receipt['reason']=='provisional_glyph_not_verified_in_offline_cache'
    assert receipt['uncertain'] is True and receipt['label']==''
    assert receipt['new_cost_usd']==0 and receipt['cached'] is False
    assert page['new_cost_usd']==0
    assert page['cached'] is True,'no optional marker request was dispatched; the cached primary read stays cached'
