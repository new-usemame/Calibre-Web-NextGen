"""A physical page turn can continue a genuine Roman-reference range."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import assemble,vision_pipeline
_helper=Path(__file__).with_name('test_round6_native_reference_range_boundaries.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round6_native_reference_range_boundaries.py'
_spec=importlib.util.spec_from_file_location('native_continuous_reference_fixture',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit

def test_continuous_roman_reference_range_preserves_authored_dash(tmp_path):
    left='The cited pages are xiv-'
    right='xv, with a further explanation.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97);p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97);q.insert_text((54,90),right,fontsize=7)
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[54,582,400,592])],
               1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[54,82,400,94])]}
        docs=h.fixture.publish(tmp_path,doc,pages)
        assert h.fixture.h.body_texts(docs)==[left+right]
    finally:doc.close()
