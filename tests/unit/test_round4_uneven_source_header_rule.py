"""Independent uneven source-rule evidence; existing ownership assertions untouched."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pixels
from cps.services.reflow.source_display import SourceDisplay

path=Path(__file__).with_name('test_round4_scanned_source_header_ownership.py')
spec=importlib.util.spec_from_file_location('uneven_source_header_fixture',path)
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

def rule(page,mode='thin'):
    # A continuous horizontal rule has a small scan slant/uneven baseline.
    # Its local stroke is .5pt despite a whole-component y extent above 3pt.
    points=[(23,32),(40,31),(200,31),(230,34),(280,34)]
    for a,b in zip(points,points[1:]):page.draw_line(a,b,width=4 if mode=='thick' else .5)
    if mode=='branch':page.draw_line((170,31),(170,49),width=.5)

def fixture_pages():
    result=[]
    for n in range(4):
        p=f.h.InkPage()
        p.text(f.TITLE if n==0 else ['An opening cobalt chapter','A continuing cobalt chapter','The final cobalt chapter'][n-1], 'head','heading' if n==0 else 'running_head',x=90,y=22,size=10)
        rule(p.page)
        p.text(str(n+41),'folio','folio',x=272,y=22,size=7)
        p.text([f.BODY,'Every ordinary word retains its printed paragraph order.'],'body',y=125,size=8)
        result.append(p)
    return result

def test_uneven_thin_rule_preserves_original_header_ownership(tmp_path,monkeypatch):
    _,_,docs=f.h.convert_ink(tmp_path,monkeypatch,fixture_pages())
    visible=[f.h.plain(n) for n in f.h.body_nodes(docs)]
    assert f.TITLE not in visible,'Thin local stroke on a continuous uneven source rule must not block corroborated header ownership'
    assert sum(f.BODY in text for text in visible)==4

@pytest.mark.parametrize('mode',['branch','thick'])
def test_uneven_rule_cannot_hide_vertical_branch_or_thick_stripe(mode):
    p=f.h.InkPage()
    try:
        p.text(f.TITLE,'head','heading',x=90,y=22,size=10)
        rule(p.page,mode)
        display=SourceDisplay(p.doc,0)
        words=p.page.get_text('words')
        box=[min(w[0] for w in words),min(w[1] for w in words),max(w[2] for w in words),max(w[3] for w in words)]
        assert vision_pixels.header_separating_rule(display,box) is None,'Connected branch and thick local strokes cannot prove standalone source separators'
    finally:p.doc.close()
