"""SOURCE-free nearby captions cannot truncate a complete column-order note."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_fixture=Path(__file__).with_name('test_round5_narrow_gutter_note_reading_order.py')
if not _fixture.exists():
    _fixture=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round5_narrow_gutter_note_reading_order.py'
_spec=importlib.util.spec_from_file_location('foreign_caption_note_fixture',_fixture)
f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)
h=f.h
pytestmark=pytest.mark.unit


def with_crossing_caption(mirror=False,complete=False):
    p,body,first,head,last=f.make_page(mirror=mirror,complete=complete,prepartitioned=not complete)
    # Unrelated short caption words span the otherwise empty note gutter.
    # The note hint is coarse; caption source ownership is independently given.
    caption='A separately printed diagram identifies the coastal comparison.'
    p.page.insert_text((15,278),caption,fontsize=4.1,fontname='unicode')
    p.blocks.append(h.model_block(caption,'separate-caption','caption',[13,269,282,282]))
    first_note=next(b for b in p.blocks if b['id']=='first')
    first_note['bbox']=[43,658,940,865]
    return p,body,first,head,last,caption


def selected_notes(docs):
    refs,notes=h.assert_exact_returns(docs,2)
    targets={(name,n.get('id')):n for name,n in notes}
    return {''.join(r.itertext()):targets[h.destination(name,r.get('href'))] for name,r in refs}


@pytest.mark.parametrize('mirror',[False,True])
def test_complete_two_column_note_survives_unrelated_caption_crossing_its_gutter(tmp_path,monkeypatch,mirror):
    p,body,first,head,last,caption=with_crossing_caption(mirror=mirror)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    selected=selected_notes(docs)
    assert f.note_text(selected['*'])==first+' '+head,'unrelated nearby caption ink must not erase or interleave the independently read right-column note continuation'
    assert f.note_text(selected['†'])==last,'a separate complete citation keeps its physical note owner'
    assert h.body_texts(docs)==[body]
    visible=[h.plain(el) for _,root in docs.items() for el in root.findall('.//x:p',h.NS)]
    assert visible.count(caption)==1,'the separate caption remains once outside either note'


def test_genuine_complete_separate_notes_are_not_merged_by_caption_neighborhood(tmp_path,monkeypatch):
    p,body,first,head,last,_=with_crossing_caption(complete=True)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    selected=selected_notes(docs)
    assert f.note_text(selected['*'])==first
    assert f.note_text(selected['†'])==head+' '+last
    assert h.body_texts(docs)==[body]
