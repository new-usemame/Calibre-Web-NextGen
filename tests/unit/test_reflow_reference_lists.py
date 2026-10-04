"""Printed leader/reference rows retain their item boundaries in reflow."""
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble,build_epub,extract,skeleton
pytestmark=pytest.mark.unit

def line(text,y,x=50,right=400,size=12,flags=0):
    box=(x,y,right,y+size)
    return extract.Line([extract.Span(text,size,'Times-Roman',flags,box)],box)

def rendered(lines):
    box=(min(l.bbox[0] for l in lines),min(l.bbox[1] for l in lines),max(l.bbox[2] for l in lines),max(l.bbox[3] for l in lines))
    raw=extract.RawPage(20,450,700,[extract.Block(0,box,lines)])
    style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style)
    book=assemble.assemble([skel],style,raw_pages=[raw])
    assert book.conservation.ok,book.conservation.to_dict()
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+build_epub.page_fragment(book,20)+'</root>')
    return root,book

def test_source_index_leader_rows_are_separate_readable_items():
    texts=['Table 1: Hot and Cold Qualities........45','Table 2: Dry and Moist Qualities........46','Table 3: Elements by Quality........46']
    root,book=rendered([line(t,120+i*18) for i,t in enumerate(texts)])
    assert [''.join(n.itertext()) for n in root.findall('.//li')]==texts
    assert book.pages[20][0].kind=='list'


def test_source_index_heading_does_not_become_an_entry():
    texts=['Table 1: Hot and Cold Qualities........45','Table 2: Dry and Moist Qualities........46','Table 3: Elements by Quality........46']
    root,_=rendered([line('Index of Tables',70,x=160,right=290,size=18,flags=16)]+[line(t,120+i*18) for i,t in enumerate(texts)])
    assert [''.join(n.itertext()) for n in root.findall('.//li')]==texts
    assert any(''.join(n.itertext())=='Index of Tables' for n in root if n.tag in ('h1','h2','h3','h4','h5','h6'))


def test_source_index_hanging_continuation_stays_with_its_reference():
    lines=[line('Table 1: A long source entry',120,right=310),line('with its continuation........45',138,x=68),line('Table 2: A second entry........46',156),line('Table 3: A third entry........51',174)]
    root,_=rendered(lines)
    assert [''.join(n.itertext()) for n in root.findall('.//li')]==['Table 1: A long source entry with its continuation........45','Table 2: A second entry........46','Table 3: A third entry........51']


@pytest.mark.parametrize('texts,right_edges',[
    (['The source prose continues here','and does not contain printed leaders','or any page references.'],[400]*3),
    (['A source row........45','Unfinished source paragraph begins','and continues here.'],[400]*3),
    (['First row........45','Second row........46','Third row........51'],[400,320,400]),
])
def test_unproved_reference_layout_remains_prose(texts,right_edges):
    root,_=rendered([line(t,120+i*18,right=r) for i,(t,r) in enumerate(zip(texts,right_edges))])
    assert not root.findall('.//li')
