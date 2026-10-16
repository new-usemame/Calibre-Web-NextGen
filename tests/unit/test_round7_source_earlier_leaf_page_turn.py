"""Actual page-turn SOURCE table/footer ordering, separate from samepage leaf case."""
import pytest
from tests.unit.round7_recovery_test_support import h
from tests.unit.test_round7_source_object_seed_and_leaf_footer_continuity import LEFT,RIGHT,LATER,NOTE,object_at,contexts

@pytest.mark.parametrize('inflow_note',[False,True],ids=['earlier-leaf-footer','same-column-inflow-note'])
def test_page_turn_source_table_and_genuine_note_use_physical_leaf_ownership(tmp_path,monkeypatch,inflow_note):
    a=h.InkPage();b=h.InkPage()
    a.text(LEFT,'left',x=22,y=55,size=6,next=True)
    object_at(b,'earlier-table',[25,170,130,245],role='table',touching=330)
    b.text(NOTE,'footer','footnote',x=22,y=330,size=5,marker='*')
    b.blocks[-1]['bbox'][2]=135/h.W*1000
    if inflow_note:b.text('A distinct physical note.','inflow','footnote',x=170,y=35,size=5,marker='')
    b.text(['the silver crystals remained','beside the river.'],'right',x=170,y=55,size=6,previous=True,model=RIGHT)
    b.text(LATER,'later',x=170,y=135,size=5)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[a,b])
    contexts(path,report,docs)
    notes=[n for root in docs.values() for n in root.findall('.//x:aside',h.NS) if n.get('id')=='v4-note-1-footer'];assert len(notes)==1 and NOTE in h.plain(notes[0]);assert all(NOTE not in s for s in h.body_texts(docs))
    expected=[LEFT,RIGHT,LATER] if inflow_note else [LEFT+' '+RIGHT,LATER]
    assert h.body_texts(docs)==expected
    if not inflow_note:
        joined=next(n for n in h.body_nodes(docs) if h.plain(n)==expected[0]);assert not joined.findall('.//x:a[@class="source-disclosure-ref"]',h.NS)
        assert {int(s.get('data-source-page')) for s in joined.findall('.//x:span',h.NS) if s.get('data-source-block') in ('left','right')}=={0,1}
