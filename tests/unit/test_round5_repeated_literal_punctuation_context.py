"""SOURCE-free repeated literal endpoint clusters need source-instance context.

The same spelling elsewhere cannot veto a uniquely context-bound final source
occurrence. Conversely two indistinguishable endpoint trials grant no proof.
"""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_repeated_punctuation_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(cross_page,tight):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    prefixes=['The early observers recorded how the levels ',
              'while later workers found that the deeper currents also ']
    rows=[];ends=[];suffixes=[]
    for prefix,y in zip(prefixes,[40,57]):
        text=prefix+'fall';end=22+pymupdf.get_text_length(text,fontsize=6)
        p.page.insert_text((22,y),text,fontsize=6)
        p.page.insert_text((end+3,y),';',fontsize=6)
        rows.append(text+';');ends.append(end);suffixes.append(end+3)
    left=' '.join(rows)
    p.blocks.append(h.model_block(left,'left','body',[20,29,ends[-1] if tight else 282,61]))
    original_finish=p.finish
    def finish():
        image,words,answer=original_finish()
        assert sum(w['text']=='fall' for w in words)==2
        assert sum(w['text']==';' for w in words)==2
        assert len(vision_pipeline._source_punctuation_suffixes(words)['fall;'])==2
        return image,words,answer
    p.finish=finish
    p.figure()
    right='and consequently the remaining observations complete the same account.'
    q.text(right,'right',x=22,y=230 if not cross_page else 70,size=6)
    later='A separate concluding paragraph retains its printed boundary.'
    q.text(later,'later',x=22,y=285 if not cross_page else 130,size=6)
    return ([p,q] if cross_page else [p]),left,right,later,ends,suffixes


@pytest.mark.parametrize('cross_page',[False,True])
@pytest.mark.parametrize('tight',[False,True])
def test_repeated_literal_clusters_bind_only_contextual_final_occurrence(tmp_path,monkeypatch,cross_page,tight):
    pages,left,right,later,ends,suffixes=fixture(cross_page,tight)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    record=next(b for b in report['pages'][0]['blocks'] if b['id']=='left')
    assert record['printed_tail']=='fall;','contextual final occurrence remains provable despite the earlier identical spelling'
    box=record['printed_tail_box']
    assert box[1]>45 and box[3]<61,'the endpoint must belong to the final physical row, never the earlier cluster'
    assert box[0]<ends[-1] and box[2]>suffixes[-1]
    assert record['printed_tail_row'] is not None and record['printed_tail_row']!=record['printed_head_row']
    assert any(b[0]<=box[0] and b[2]>=box[2] and b[1]<=box[1] and b[3]>=box[3] for b in record['source_regions']),'uniquely adjacent punctuation belongs to the already anchored final source occurrence even beyond a tight hint'
    texts=h.body_texts(docs);expected=left+' '+right
    assert expected in texts and texts.count(expected)==1 and later in texts
    assert expected.count('fall;')==2,'literal punctuation at both source occurrences stays unchanged'
    nodes=h.body_nodes(docs);joined=next(i for i,n in enumerate(nodes) if h.plain(n)==expected)
    art=next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert joined<art
    assert [h.plain(n) for n in nodes if n.get('class')=='caption']==['A connected synthetic diagram.']


def test_indistinguishable_endpoint_cluster_trials_remain_unproved():
    # A single published token supplies no contextual choice between these
    # equally literal source occurrences. Each trial maps its own endpoint.
    words=[{'text':'fall','bbox':[22,y,32,y+6],'confidence':99}
           if i%2==0 else {'text':';','bbox':[35,y,37,y+6],'confidence':99}
           for i,y in enumerate([30,30,55,55])]
    assert len(vision_pipeline._source_punctuation_suffixes(words)['fall;'])==2
    head,tail=vision_pipeline._published_pixel_edges(['fall;'],words)
    assert head is None and tail is None,'ambiguous source instances cannot be selected by spelling or first-match preference'
