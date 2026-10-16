"""Synthetic original artwork/control carriers cannot sever a proven sentence."""
from tests.fixtures.reflow_output_contracts import has_authored_substring
import pytest
from tests.unit.round7_recovery_test_support import h

LEFT='The careful observers compared the amber stones and'
RIGHT='the silver crystals beside the ordinary river.'
LATER='A separate violet conclusion retains its complete wording.'


def art(page, ident, y, role='figure', caption=True, touching=55):
    box=[35,y,260,y+42]
    page.page.draw_rect(box,color=(0,0,0),fill=(.9,.9,.9),width=2)
    page.page.draw_line((35,y),(260,y+42),color=(0,0,0),width=2)
    for x in (75,125,175,225):
        page.page.draw_line((x,y),(x,y+42),color=(0,0,0),width=1)
    for yy in (y+10,y+20,y+30):
        page.page.draw_line((35,yy),(260,yy),color=(0,0,0),width=1)
    # A connected original stroke crosses genuine source prose: the object
    # cannot be clipped cleanly and must use the complete SOURCE context.
    page.page.draw_line((120,y),(120,touching),color=(0,0,0),width=2)
    page.blocks.append(h.model_block('An original synthetic diagram.',ident,role,box))
    if caption:
        page.text('A separate synthetic object caption.',ident+'-caption','caption',x=22,y=y+53,size=6)


def fixture(kind,barrier='none'):
    left=h.InkPage();right=h.InkPage()
    ltext=LEFT+'.' if barrier=='terminal' else LEFT
    left.text(ltext,'left',x=22,y=55,size=6,next=True)
    if kind=='figures':
        art(left,'lower-figure-a',90)
        art(left,'lower-figure-b',175)
    art(right,'upper-object',90,role='table' if kind=='table' else 'figure',caption=kind!='table',touching=230)
    if barrier=='heading':right.text('A Genuine Separate Section','section','heading',x=22,y=205,size=8)
    right.text(RIGHT,'right',x=22,y=230,size=6,previous=True)
    right.text(LATER,'later',x=22,y=330,size=6)
    return [left,right],ltext


def assert_source_contexts(path,report,docs):
    import zipfile
    refs=[(name,a) for name,root in docs.items() for a in root.findall('.//x:a',h.NS)
          if a.get('class')=='source-disclosure-ref' and has_authored_substring(a.text or '', '8eea9d54b9d9adb5031e1ce6424bf1fa49ced0f94699ca9a2273703153e67531', 28) and len(a.text or '')==28]
    assert refs and report['degraded_figure_count'], 'the original inseparable object must exercise SOURCE context publication'
    with zipfile.ZipFile(path) as z:
        for home,ref in refs:
            dest,ident=h.destination(home,ref.get('href'))
            target=next(n for n in docs[dest].iter() if n.get('id')==ident)
            assert target.findall('.//x:img',h.NS)
            for im in target.findall('.//x:img',h.NS):assert 'OEBPS/'+im.get('src') in z.namelist()
            assert any(a.text=='Return' and h.destination(dest,a.get('href'))==(home,ref.get('id'))
                       for a in target.findall('.//x:a',h.NS))


@pytest.mark.parametrize('kind',['figures','table'])
def test_source_contexts_and_captions_move_after_complete_page_turn_sentence(tmp_path,monkeypatch,kind):
    pages,left=fixture(kind)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    expected=left+' '+RIGHT
    texts=h.body_texts(docs)
    assert texts==[expected,LATER], 'a source-proven unfinished sentence must resume before generated object controls and captions'
    assert_source_contexts(path,report,docs)
    nodes=h.body_nodes(docs);joined=next(n for n in nodes if h.plain(n)==expected)
    assert not joined.findall('.//x:a[@class="source-disclosure-ref"]',h.NS), 'object controls cannot be placed inside the joined authored sentence'
    assert next(i for i,n in enumerate(nodes) if n is joined)<next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert len([n for n in nodes if n.get('class')=='caption'])==(3 if kind=='figures' else 0)
    source_spans=[s for s in joined.findall('.//x:span',h.NS) if s.get('data-source-block') in ('left','right')]
    assert {int(s.get('data-source-page')) for s in source_spans}=={0,1}


@pytest.mark.parametrize('kind',['figures','table'])
@pytest.mark.parametrize('barrier',['terminal','heading'])
def test_source_context_relocation_preserves_genuine_sentence_or_heading_boundary(tmp_path,monkeypatch,kind,barrier):
    pages,left=fixture(kind,barrier)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    assert h.body_texts(docs)==[left,RIGHT,LATER]
    assert_source_contexts(path,report,docs)
    if barrier=='heading':
        assert any(n.tag.endswith('}h2') and h.plain(n)=='A Genuine Separate Section' for n in h.body_nodes(docs))
