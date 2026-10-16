"""SOURCE-free completed Roman labels remain structural paragraph boundaries.

Lowercase text after a completed item is not an unfinished parenthetical
sentence. Genuine parenthetical article prose still continues with literal ink.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_completed_item_source_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(cross_page,item):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    left='The quiet observers refer to the following illustration'
    right=('(ii) the separate numbered item keeps its own authored boundary.' if item
           else '(the enclosed curves), we may compare the later observations.')
    p.text(left,'left',x=22,y=55,size=6)
    p.figure()
    p.page.draw_line((120,100),(120,45),color=(0,0,0),width=2)
    q.text(right,'right',x=22,y=230 if not cross_page else 75,size=6)
    later='A separate concluding paragraph retains its authored boundary.'
    q.text(later,'later',x=22,y=285 if not cross_page else 130,size=6)
    return ([p,q] if cross_page else [p]),left,right,later


@pytest.mark.parametrize('cross_page',[False,True])
def test_completed_roman_item_cannot_join_unfinished_prose(tmp_path,monkeypatch,cross_page):
    pages,left,right,later=fixture(cross_page,True)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    records={b['id']:b for page in report['pages'].values() for b in page['blocks']}
    assert records['left']['printed_tail']=='illustration' and records['right']['printed_head']=='(ii)'
    assert records['right']['first_line_x']==22 and not records['right']['continues_previous']
    texts=h.body_texts(docs)
    assert left+' '+right not in texts,'a completed Roman item label remains an authored boundary despite exact endpoints and lowercase following text'
    assert left in texts and right in texts and later in texts
    nodes=h.body_nodes(docs)
    art=next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    origin=next(i for i,n in enumerate(nodes) if h.plain(n)==left)
    item=next(i for i,n in enumerate(nodes) if h.plain(n)==right)
    assert origin<art<item


@pytest.mark.parametrize('cross_page',[False,True])
def test_genuine_parenthetical_article_continuation_is_not_an_item(tmp_path,monkeypatch,cross_page):
    pages,left,right,later=fixture(cross_page,False)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    texts=h.body_texts(docs);expected=left+' '+right
    assert expected in texts and texts.count(expected)==1 and later in texts
    assert expected.count('(the enclosed curves),')==1
    nodes=h.body_nodes(docs)
    joined=next(i for i,n in enumerate(nodes) if h.plain(n)==expected)
    art=next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert joined<art and report['degraded_figure_count']==1
