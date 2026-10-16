"""SOURCE-free exact word+punctuation endpoint proof and territory ownership.

Optical tokens may split the literal published endpoint. Only a uniquely
adjacent same-row exact concatenation supplies a union source box. Distant ink,
other rows, changed letters and actual terminal punctuation remain barriers.
"""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_split_punctuation_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(cross_page,tight,barrier='none'):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    prefix='The quiet observers recorded the current as the levels '
    word='fall';punct='.' if barrier=='terminal' else ';'
    left=prefix+word+punct
    x=22;y=55;size=6
    p.page.insert_text((x,y),prefix+word,fontsize=size)
    end=x+pymupdf.get_text_length(prefix+word,fontsize=size)
    suffix_x=end+(35 if barrier=='wide_gap' else 3)
    suffix_y=y+(18 if barrier=='cross_row' else 0)
    p.page.insert_text((suffix_x,suffix_y),punct,fontsize=size)
    p.blocks.append(h.model_block(left,'left','body',[20,44,end if tight else 282,59]))
    if barrier=='changed':
        left=left.replace('fall;','decline;');p.blocks[-1]['text']=left
        p.blocks[-1]['low_confidence']=[len(left.split())-1]
    original_finish=p.finish
    def finish():
        image,words,answer=original_finish()
        tail=next(w for w in words if w['text']==word)
        suffix=next(w for w in words if w['text']==punct)
        assert suffix['bbox'][0]>tail['bbox'][2],'the optical suffix is a separate source instance'
        return image,words,answer
    p.finish=finish
    p.figure()
    right='and therefore the following observations complete this same account.'
    q.text(right,'right',x=22,y=230 if not cross_page else 70,size=6)
    later='A separate concluding paragraph retains its printed boundary.'
    q.text(later,'later',x=22,y=285 if not cross_page else 130,size=6)
    return ([p,q] if cross_page else [p]),left,right,later,end,suffix_x,suffix_y


@pytest.mark.parametrize('cross_page',[False,True])
@pytest.mark.parametrize('tight',[False,True])
def test_split_adjacent_semicolon_supplies_exact_endpoint_and_union_territory(tmp_path,monkeypatch,cross_page,tight):
    pages,left,right,later,end,suffix_x,suffix_y=fixture(cross_page,tight)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    record=next(b for b in report['pages'][0]['blocks'] if b['id']=='left')
    assert record['printed_tail']=='fall;','literal same-row optical word+suffix must bind the exact published endpoint'
    assert record['printed_tail_row'] is not None and record['printed_tail_row']==record['printed_head_row'],'literal endpoint cluster retains its actual physical source row'
    box=record['printed_tail_box'];assert box[0]<end and box[2]>suffix_x
    assert any(b[0]<=box[0] and b[2]>=box[2] and b[1]<=box[1] and b[3]>=box[3] for b in record['source_regions']),'the source-proved suffix must stay within its BODY territory even when the raw hint ends at the letters'
    texts=h.body_texts(docs);expected=left+' '+right
    assert expected in texts and texts.count(expected)==1 and later in texts
    nodes=h.body_nodes(docs);joined=next(i for i,n in enumerate(nodes) if h.plain(n)==expected)
    art=next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert joined<art,'split optical punctuation never causes a source-proved unfinished paragraph to break around art'
    assert [h.plain(n) for n in nodes if n.get('class')=='caption']==['A connected synthetic diagram.']


@pytest.mark.parametrize('barrier',['wide_gap','cross_row','changed','terminal'])
def test_split_punctuation_endpoint_needs_exact_adjacent_source_and_unfinished_clause(tmp_path,monkeypatch,barrier):
    pages,left,right,later,_,_,_=fixture(True,False,barrier)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    record=next(b for b in report['pages'][0]['blocks'] if b['id']=='left')
    if barrier!='terminal':assert record['printed_tail'] is None,'distant/cross-row punctuation or changed letters cannot prove the published literal endpoint'
    texts=h.body_texts(docs)
    assert left+' '+right not in texts,'terminal punctuation cannot be stripped and insufficient source proof cannot join'
    assert left in texts and right in texts and later in texts
