"""Actual typed requests, settlement, canonical source and final builder together."""
import json
import zipfile
import pytest
from tests.unit.test_reflow_structural_ops import source, texts, X
from tests.unit.test_reflow_typed_transport import Session, reply
from cps.services.reflow import pipeline, build_epub, extract
from cps.services.reflow.ledger import Ledger

pytestmark=pytest.mark.unit


class WorkflowSession:
    def __init__(self,approve=True):self.calls=[];self.approve=approve
    def get(self,url,**kwargs):return Session().get(url,**kwargs)
    def post(self,*args,**kwargs):
        payload=json.loads(kwargs['data']);self.calls.append(payload)
        body=json.loads(payload['messages'][1]['content'][1]['text'])
        response=body['empty_response'].copy()
        if payload['model'].endswith('luna'):
            response['select']=[next(c['candidate_id'] for c in body['source']['candidates'] if c['element_id']=='e0' and c['kind']=='heading')]
        else:response['approve']=body['source']['verification']['proposed_ids'] if self.approve else []
        data=reply(json.dumps(response));data['model']=payload['model']
        return Session(data).post()


def prepared_result(source):
    book,doc,_=source
    return pipeline.ReflowResult(book=book,raw_pages=[extract.read_page(doc,0)],
        page_html={0:build_epub.page_fragment(book,0)},fingerprint=extract.document_fingerprint(doc))


@pytest.mark.parametrize('approve',[True,False])
def test_real_two_stage_pipeline_only_builds_verifier_decision_and_cache_replays_free(source,approve):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    book,doc,tmp=source;session=WorkflowSession(approve);client=TwoStageClient('test',enabled=True,session=session)
    ledger=Ledger(str(tmp/'job1'),cap_usd=1);cache=pipeline.PageCache(tmp/'cache')
    result=run_structural(doc,client=client,ledger=ledger,cache=cache,prepared_result=prepared_result(source))
    assert len(session.calls)==2 and ledger.spent()==pytest.approx(2*.000123456789)
    assert result.structural['proposed_operations']==1
    assert result.structural['requested_models']=={stage.model_id:1 for stage in client.stages.values()}
    assert result.structural['approved_operations']==int(approve)
    target=tmp/'result.epub'
    build_epub.build(book,str(target),doc=doc,page_html=result.page_html,source_pages=result.source_pages,operation_plans=result.operation_plans)
    assert any(h.text=='Learning the sky' for r in texts(target) for h in r.iter(X+'h2'))==approve
    second=Ledger(str(tmp/'other-owner-job'),cap_usd=1)
    result=run_structural(doc,client=client,ledger=second,cache=cache,prepared_result=prepared_result(source))
    assert len(session.calls)==2 and second.spent()==0
    assert result.structural['cached_stages']==2
    assert all(record.get('cost_usd',0)==0 for record in result.stage_records)


def test_budget_can_stop_between_stages_without_publishing_proposal(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    book,doc,tmp=source;session=WorkflowSession();client=TwoStageClient('test',enabled=True,session=session)
    ledger=Ledger(str(tmp/'cap'),cap_usd=.01)
    result=run_structural(doc,client=client,ledger=ledger,cache=pipeline.PageCache(tmp/'cache'),prepared_result=prepared_result(source))
    assert len(session.calls)==1
    assert not result.operation_plans and result.stopped=='cost_cap'
    assert set(result.page_html)==set(book.pages) and result.source_pages[0].html==result.page_html[0]
    assert result.structural['proposed_operations']==1 and result.structural['approved_operations']==0


def test_quality_gate_prevents_network_while_source_and_coverage_are_prepared(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    _,doc,tmp=source;session=WorkflowSession();client=TwoStageClient('test',session=session)
    result=run_structural(doc,client=client,ledger=Ledger(str(tmp/'gate'),cap_usd=1),prepared_result=prepared_result(source))
    assert session.calls==[] and result.structural['eligible']==1
    assert result.structural['unreviewed']==1 and result.stopped=='quality_gate'


def test_provider_bill_over_request_bound_is_recorded_and_stops_before_approval(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    _,doc,tmp=source
    class Expensive(WorkflowSession):
        def post(self,*args,**kwargs):
            result=super().post(*args,**kwargs);result.json()['usage']['cost']=.5
            return result
    session=Expensive();ledger=Ledger(str(tmp/'overbound'),cap_usd=1)
    result=run_structural(doc,client=TwoStageClient('test',enabled=True,session=session),ledger=ledger,
        cache=pipeline.PageCache(tmp/'cache'),prepared_result=prepared_result(source))
    assert len(session.calls)==1 and ledger.spent()==.5
    assert result.stopped=='billing_bound' and not result.operation_plans
    assert result.outcomes[0].cost_usd==.5
    assert result.stage_records[0]['status']=='rejected'


def test_two_jobs_same_source_cannot_race_into_two_paid_proposals(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import pymupdf
    book,doc,tmp=source;entered=Event();release=Event()
    class Blocking(WorkflowSession):
        def post(self,*args,**kwargs):
            if json.loads(kwargs['data'])['model'].endswith('luna'):
                entered.set();assert release.wait(10)
            return super().post(*args,**kwargs)
    first=Blocking();second=WorkflowSession();cache=pipeline.PageCache(tmp/'shared')
    def run(session,name):
        with pymupdf.open(doc.name) as own:
            return run_structural(own,client=TwoStageClient('test',enabled=True,session=session),
                ledger=Ledger(str(tmp/name),cap_usd=1),cache=cache,
                prepared_result=prepared_result((book,own,tmp)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        active=pool.submit(run,first,'first-private-job');assert entered.wait(10)
        other=pool.submit(run,second,'other-user-job').result(timeout=10)
        release.set();completed=active.result(timeout=10)
    assert other.stopped=='prior_request_pending' and second.calls==[]
    assert other.spend_usd==0 and other.pending_usd==0
    assert 'first-private-job' not in json.dumps(other.structural)
    assert len(first.calls)==2 and completed.structural['approved_operations']==1


def test_two_enabled_jobs_without_durable_claim_store_refuse_before_preflight(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    from cps.services.reflow.structural_ops import ContractError
    from concurrent.futures import ThreadPoolExecutor
    _,doc,tmp=source
    class NoNetwork(WorkflowSession):
        def get(self,*args,**kwargs):raise AssertionError('preflight must not run without durable claims')
    def run(index):
        session=NoNetwork();ledger=Ledger(str(tmp/('uncached-job-%d'%index)),cap_usd=1)
        with pytest.raises(ContractError,match='durable'):
            run_structural(doc,client=TwoStageClient('test',enabled=True,session=session),
                           ledger=ledger,prepared_result=prepared_result(source))
        assert session.calls==[] and ledger.entries()==[]
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(2)))


def test_enabled_claim_store_also_requires_durable_billing_ledger(source):
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    from cps.services.reflow.structural_ops import ContractError
    from cps.services.reflow.operation_cache import OperationCache
    _,doc,tmp=source;session=WorkflowSession();cache=OperationCache(tmp/'typed-cache')
    client=TwoStageClient('test',enabled=True,session=session)
    with pytest.raises(ContractError,match='ledger'):
        run_structural(doc,client=client,cache=cache,prepared_result=prepared_result(source))
    assert session.calls==[] and not cache.directory.exists()
    result=run_structural(doc,client=client,cache=cache,ledger=Ledger(str(tmp/'billed'),cap_usd=1),
                          prepared_result=prepared_result(source))
    assert result.structural['approved_operations']==1 and len(session.calls)==2
    assert len(list(cache.directory.glob('*/*.json')))==2
