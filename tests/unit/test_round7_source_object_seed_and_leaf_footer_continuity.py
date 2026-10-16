"""Original SOURCE rows, not displaced model hints, govern prose continuity."""
from tests.fixtures.reflow_output_contracts import has_authored_substring
import pytest,copy
from tests.unit.round7_recovery_test_support import h
from cps.services.reflow import vision_pipeline as vp
LEFT='The amber observers carried the clear stones and'
RIGHT='the silver crystals remained beside the river.'
LATER='A separate violet conclusion remains complete.'
CAPTION='The separate circular synthetic diagram.'
NOTE='A genuine footer records a local observation.'

def object_at(page,ident,box,role='figure',touching=None):
    page.page.draw_rect(box,color=(0,0,0),fill=(.9,.9,.9),width=2)
    page.page.draw_line((box[0],box[1]),(box[2],box[3]),color=(0,0,0),width=2)
    if touching is not None:page.page.draw_line((box[0]+20,box[1]),(box[0]+20,touching),color=(0,0,0),width=2)
    page.blocks.append(h.model_block('A synthetic source object.',ident,role,box))
    return page.blocks[-1]

def contexts(path,report,docs):
    import zipfile
    refs=[(n,a) for n,root in docs.items() for a in root.findall('.//x:a',h.NS) if a.get('class')=='source-disclosure-ref' and len(a.text or "")==28 and has_authored_substring(a.text or "", "8eea9d54b9d9adb5031e1ce6424bf1fa49ced0f94699ca9a2273703153e67531", 28)]
    assert refs and report['degraded_figure_count']
    with zipfile.ZipFile(path) as z:
      for home,ref in refs:
        dest,ident=h.destination(home,ref.get('href'));target=next(n for n in docs[dest].iter() if n.get('id')==ident)
        assert target.findall('.//x:img',h.NS)
        for im in target.findall('.//x:img',h.NS):assert 'OEBPS/'+im.get('src') in z.namelist()
        assert any(a.text=='Return' and h.destination(dest,a.get('href'))==(home,ref.get('id')) for a in target.findall('.//x:a',h.NS))

def make_shifted(barrier):
    a=h.InkPage();b=h.InkPage();left=LEFT+'.' if barrier=='terminal' else LEFT
    a.text(left,'left',x=22,y=55,size=6,next=True)
    object_at(a,'trailing',[35,90,260,132],touching=55)
    fig=object_at(b,'leading',[35,90,260,160],touching=240)
    # The model's broad object seed includes the complete original caption and
    # overlaps the next prose line. The caption's own hint is 50pt too low.
    fig['bbox']=[35/h.W*1000,90/h.H*1000,260/h.W*1000,237/h.H*1000]
    cap=b.text(CAPTION,'caption','caption',x=42,y=185,size=6)
    cap['bbox']=[21/h.W*1000,230/h.H*1000,280/h.W*1000,245/h.H*1000]
    if barrier=='ambiguous':b.page.insert_text((42,204),CAPTION,fontsize=6)
    if barrier=='heading':b.text('A Genuine Separate Section','heading','heading',x=22,y=215,size=7)
    b.text(RIGHT,'right',x=42 if barrier=='indent' else 22,y=240,size=6,previous=True)
    b.text(LATER,'later',x=22,y=330,size=6)
    return [a,b],left

@pytest.mark.parametrize('barrier',['none','terminal','heading','indent','ambiguous'])
def test_unique_source_caption_inside_adjacent_art_seed_preserves_continuation(tmp_path,monkeypatch,barrier):
    pages,left=make_shifted(barrier);path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    expected=[left+' '+RIGHT,LATER] if barrier=='none' else [left,RIGHT,LATER]
    assert h.body_texts(docs)==expected
    contexts(path,report,docs)
    if barrier=='none':
      caps=[n for n in h.body_nodes(docs) if n.get('class')=='caption'];assert [h.plain(n) for n in caps]==[CAPTION]
      joined=next(n for n in h.body_nodes(docs) if h.plain(n)==expected[0]);assert not joined.findall('.//x:a[@class="source-disclosure-ref"]',h.NS)
      assert {int(s.get('data-source-page')) for s in joined.findall('.//x:span',h.NS) if s.get('data-source-block') in ('left','right')}=={0,1}
    if barrier=='heading':assert any(n.tag.endswith('}h2') and h.plain(n)=='A Genuine Separate Section' for n in h.body_nodes(docs))

def make_leaf(barrier):
    p=h.InkPage();left=LEFT+'.' if barrier=='terminal' else LEFT
    p.text(['The amber observers carried','the clear stones and'+('.' if barrier=='terminal' else '')],'left',x=22,y=95,size=6,next=True,model=left)
    p.blocks[-1]['bbox'][2]=135/h.W*1000
    object_at(p,'table',[25,170,130,245],role='table',touching=106)
    note_y=118 if barrier=='inflow-note' else 330
    p.text(NOTE,'footer','footnote',x=22,y=note_y,size=5,marker='*');p.blocks[-1]['bbox'][2]=135/h.W*1000
    if barrier=='heading':p.text('A Genuine Separate Section','section','heading',x=170,y=32,size=6)
    p.text(['the silver crystals remained','beside the river.'],'right',x=190 if barrier=='indent' else 170,y=55,size=6,previous=True,model=RIGHT)
    p.text(LATER,'later',x=170,y=135,size=5)
    return [p],left

@pytest.mark.parametrize('barrier',['none','terminal','heading','indent','inflow-note'])
def test_earlier_leaf_source_table_and_footer_do_not_interrupt_right_leaf_prose(tmp_path,monkeypatch,barrier):
    pages,left=make_leaf(barrier);path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    expected=[left+' '+RIGHT,LATER] if barrier=='none' else [left,RIGHT,LATER]
    assert h.body_texts(docs)==expected
    contexts(path,report,docs)
    notes=[n for root in docs.values() for n in root.findall('.//x:aside',h.NS) if n.get('id')=='v4-note-0-footer'];assert len(notes)==1 and NOTE in h.plain(notes[0])
    assert all(NOTE not in t for t in h.body_texts(docs))
    if barrier=='none':
      joined=next(n for n in h.body_nodes(docs) if h.plain(n)==expected[0]);assert not joined.findall('.//x:a[@class="source-disclosure-ref"]',h.NS)
    if barrier=='heading':assert any(n.tag.endswith('}h2') and h.plain(n)=='A Genuine Separate Section' for n in h.body_nodes(docs))
