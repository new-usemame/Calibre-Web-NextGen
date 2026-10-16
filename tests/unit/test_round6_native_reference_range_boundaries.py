"""Printed reference ranges must not become divided lexical words at page turns."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import assemble, vision_pipeline
_helper=Path(__file__).with_name('test_round6_native_source_margin_guards.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round6_native_source_margin_guards.py'
_spec=importlib.util.spec_from_file_location('native_reference_range_fixture',_helper)
fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(fixture)
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('reference',['xiv-','14-'])
def test_page_reference_range_does_not_absorb_new_index_entry(tmp_path,reference):
    left='Festival reference, '+reference
    right='Bench guides, 42.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97)
        p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97)
        q.insert_text((54,90),right,fontsize=7)
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[54,582,400,592])],
               1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[54,82,400,94])]}
        docs=fixture.publish(tmp_path,doc,pages)
        assert fixture.h.body_texts(docs)==[left,right], 'A Roman or numeric page reference ending in a range dash cannot consume the next independent entry.'
    finally:doc.close()

@pytest.mark.parametrize('mode',['division','compound','prose'])
def test_real_division_compound_and_unfinished_prose_still_join(tmp_path,mode):
    if mode=='division':
        left='The arriving trav-';right='elers entered the courtyard.'
        expected='The arriving travelers entered the courtyard.'
        witness='The travelers remain nearby.'
    elif mode=='compound':
        left='The observers inspect the bridge-';right='side structure.'
        expected='The observers inspect the bridge-side structure.'
        witness='The bridge-side structure remains visible.'
    else:
        left='The trays were painted by';right='Drawer beside the workbench.'
        expected=left+' '+right;witness='A separate authored paragraph begins here.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97)
        p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97)
        q.insert_text((54,90),right,fontsize=7)
        q.insert_text((54,160),witness,fontsize=7)
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[54,582,400,592])],
               1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[54,82,400,94]),
                  assemble.Element('p',runs=[['t',witness]],pno=1,bbox=[54,152,400,164])]}
        docs=fixture.publish(tmp_path,doc,pages)
        assert fixture.h.body_texts(docs)==[expected,witness]
    finally:doc.close()
