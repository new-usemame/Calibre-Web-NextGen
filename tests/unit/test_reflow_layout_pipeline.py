"""Actual transport/cache, source compilation and EPUB publication together."""
import json
import pytest
from tests.unit.test_reflow_structural_ops import source,texts,X
from tests.unit.test_reflow_structural_pipeline import prepared_result
from tests.unit.test_reflow_layout_transport import Session
from cps.services.reflow import layout_pipeline as layout,pipeline,build_epub
from cps.services.reflow.ledger import Ledger
pytestmark=pytest.mark.unit


class Workflow(Session):
    def __init__(self,fixture,approved=True):
        super().__init__();self.approved=approved
        from tests.unit.test_reflow_layout_ops import prepared,answer
        value=prepared(fixture)[-1];self.prepared=value;self.groups=answer(value)['groups'];self.groups[0]['role']='heading2'
        self.route.update(context_length=1050000,max_prompt_tokens=922000)
    def post(self,*args,**kwargs):
        wire=json.loads(kwargs['data']);identity=json.loads(wire['messages'][1]['content'])
        from cps.services.reflow import layout_wire,layout_ranges as layout_domain
        view=layout_wire.source_view(wire['messages'][:-1])
        if identity['stage']=='proposer':
            from tests.unit.test_reflow_range_choices import range_fixture
            response=range_fixture(self.prepared,self.groups)
        else:
            assert identity['stage']=='reviewer'
            response=dict(snapshot=view['snapshot'],accept=self.approved,problems=[] if self.approved else ['heading_scope'],continuation_accept=False)
        if identity['stage']=='reviewer':response['decisions']=('1' if self.approved else '0')*layout_domain.decision_count(view['decisions'])
        self.data['choices'][0]['message']['content']=json.dumps(response)
        return super().post(*args,**kwargs)


@pytest.mark.parametrize('approved',[True,False])
def test_model_layout_or_page_fallback_survives_real_epub_and_cache(source,approved):
    book,doc,tmp=source;session=Workflow(source,approved)
    client=layout.LayoutClient('test',enabled=True,session=session)
    cache=pipeline.PageCache(tmp/'cache');ledger=Ledger(str(tmp/'first'),cap_usd=1)
    result=layout.run_layout(doc,client=client,cache=cache,ledger=ledger,prepared_result=prepared_result(source))
    assert len(session.posts)==2
    assert len(result.layout_plans)==int(approved)
    assert ledger.spent()==pytest.approx(.0002)
    from cps.services.reflow import report
    payload=report.numbers(result,ledger=ledger,client=client)
    assert report.check_completion(payload,ledger)==[]
    assert 'reading order' in report.about_page(payload)
    path=tmp/'layout.epub'
    build_epub.build(book,str(path),doc=doc,page_html=result.page_html,source_pages=result.source_pages,
        layout_plans=result.layout_plans,raw_pages={r.pno:r for r in result.raw_pages})
    assert any(n.text=='Learning the sky' for r in texts(path) for n in r.iter(X+'h2'))==approved
    second=Ledger(str(tmp/'second'),cap_usd=1)
    repeated=layout.run_layout(doc,client=client,cache=cache,ledger=second,prepared_result=prepared_result(source))
    assert len(session.posts)==2 and second.spent()==0
    assert repeated.structural['cached_stages']==2


def test_unreleased_layout_prepares_fallback_without_network(source):
    _,doc,tmp=source;session=Workflow(source)
    result=layout.run_layout(doc,client=layout.LayoutClient('test',enabled=False,session=session),prepared_result=prepared_result(source))
    assert result.stopped=='quality_gate' and not result.layout_plans and not session.posts
    assert result.page_html[0]==result.source_pages[0].html


def test_cap_stop_never_publishes_an_unreviewed_proposal(source):
    _,doc,tmp=source;session=Workflow(source)
    client=layout.LayoutClient('test',enabled=True,session=session)
    # First paid answer consumes the remaining cap; review cannot be reserved.
    session.data['usage']['cost']=.01
    ledger=Ledger(str(tmp/'limited'),cap_usd=.11)
    result=layout.run_layout(doc,client=client,ledger=ledger,cache=pipeline.PageCache(tmp/'cache'),prepared_result=prepared_result(source))
    assert len(session.posts)==1 and not result.layout_plans
    assert result.stopped in ('cost_cap','billing_bound') and ledger.spent()==.01


from tests.unit.test_reflow_layout_ops import boundary_source


@pytest.mark.parametrize('rejected,continued,expected',[(0,True,[]),(1,True,[]),(0,False,[1]),(1,False,[0])])
def test_mixed_continuation_uses_paired_fallback_before_publication(boundary_source,tmp_path,rejected,continued,expected):
    from cps.services.reflow import layout_ops,extract
    from tests.unit.test_reflow_layout_ops import answer
    book,doc,sources,raws=boundary_source
    prepared=[layout_ops.prepare(book,doc,p,sources[p],raws[p],
        **(dict(previous_source=sources[0],previous_raw=raws[0]) if p else {})) for p in range(2)]
    proposals={}
    for p,value in enumerate(prepared):
        response=answer(value)
        if p==0:
            response['groups'][:1]=[dict(role='paragraph',ranges=[['a0','a1']]),dict(role='paragraph',ranges=[['a2','a3']])]
        response.update(continuation=p==1 and continued,boundary_join=None)
        proposals[value.snapshot_id]=response
    class Mixed(Session):
        def __init__(self):
            super().__init__()
            self.route.update(context_length=1050000,max_prompt_tokens=922000)
        def post(self,*args,**kwargs):
            wire=json.loads(kwargs['data']);identity=json.loads(wire['messages'][1]['content'])
            from cps.services.reflow import layout_wire,layout_ranges as layout_domain
            view=layout_wire.source_view(wire['messages'][:-1])
            if identity['stage']=='proposer':
                from tests.unit.test_reflow_range_choices import range_fixture
                pp=next(v for v in prepared if v.snapshot_id==view['source_snapshot'])
                prev=(json.loads(prepared[0].contract_json),proposals[prepared[0].snapshot_id]) if pp.page else None
                original=proposals[view['source_snapshot']]
                inc={'continue':original['continuation'],'previous':int(original['boundary_join']['left'][1:]) if original['boundary_join'] else None,'current':int(original['boundary_join']['right'][1:]) if original['boundary_join'] else None,'hyphen':original['boundary_join']['hyphen'] if original['boundary_join'] else None}
                if original['continuation'] and original['boundary_join'] is None:
                    # Explicit test semantics: the declared second main group ends
                    # at a3 and the declared current first main group starts a0.
                    inc.update(previous=3,current=0)
                response=range_fixture(pp,original['groups'],incoming=inc)
            else:
                assert identity['stage']=='reviewer'
                accepted=identity['source_identity']['snapshot']!=prepared[rejected].snapshot_id
                response=dict(snapshot=view['snapshot'],accept=accepted,problems=[] if accepted else ['paragraph_merge'],continuation_accept=False)
            if identity['stage']=='reviewer':response['decisions']='1'*layout_domain.decision_count(view['decisions'])
            self.data['choices'][0]['message']['content']=json.dumps(response)
            return super().post(*args,**kwargs)
    result=pipeline.ReflowResult(book=book,raw_pages=raws,
        page_html={p:sources[p].html for p in range(2)},fingerprint=extract.document_fingerprint(doc))
    result=layout.run_layout(doc,client=layout.LayoutClient('test',enabled=True,session=Mixed()),
        cache=pipeline.PageCache(tmp_path/'cache'),ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1),prepared_result=result)
    assert [p.prepared.page for p in result.layout_plans]==expected, result.structural
    if continued:
        assert any(p.get('reason')=='continuation_neighbor_fallback' for p in result.structural['pages'])
        target=tmp_path/'paired.epub'
        build_epub.build(book,str(target),doc=doc,source_pages=result.source_pages,raw_pages=dict(enumerate(raws)))
        body=' '.join(''.join(root.itertext()) for root in texts(target))
        assert 'cooperation resumes' in body and 'A separate source note.' in body


@pytest.mark.parametrize('blank',[False,True])
def test_conditional_figure_must_survive_packaging_before_model_admission(source,blank):
    book,doc,tmp=source
    book.figures[0]['needs_ink']=True
    if blank:
        book.figures[0]['bbox']=(450,450,490,490)
        next(e for e in book.pages[0] if e.kind=='fig').bbox=(450,450,490,490)
    result=layout.run_layout(doc,client=layout.LayoutClient('test',enabled=True,session=Workflow(source)),
        cache=pipeline.PageCache(tmp/'cache'),ledger=Ledger(str(tmp/'ledger'),cap_usd=1),prepared_result=prepared_result(source))
    assert bool(result.layout_plans) is (not blank)
    if blank:
        assert result.structural['pages'][0]['reason']=='protected_figure_not_publishable'
    built=build_epub.build(book,str(tmp/'figure.epub'),doc=doc,source_pages=result.source_pages,
        layout_plans=result.layout_plans,raw_pages={p.pno:p for p in result.raw_pages})
    assert build_epub.validate(built.path)==[]
    assert built.sidecar.get('layout_operations',{}).get('applied_pages',[])==([] if blank else [0])
