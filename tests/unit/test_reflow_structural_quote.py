"""The product quote uses actual shared request serialization without dispatch."""
import itertools
import pytest
from tests.unit.test_reflow_structural_ops import source,prepared,response
from tests.unit.test_reflow_structural_pipeline import prepared_result
from cps.services.reflow import prompts,structural_ops as ops,typed_model

pytestmark=pytest.mark.unit


def test_maximum_legal_selection_bounds_every_actual_verifier_request(source):
    from cps.services.reflow.structural_quote import measure_page
    book,doc,_=source;p=prepared(source)
    quote=measure_page(book,doc,p)
    ids=[c['candidate_id'] for c in p.candidates()]
    assert len(ids)<12  # Enumerate the complete source fixture, not a sample.
    client=typed_model.TypedStageClient('', 'verifier')
    measured=[]
    for size in range(len(ids)+1):
        for selected in itertools.combinations(ids,size):
            try:plan=p.accept(book,doc,response(p,list(selected)))
            except ops.ContractError:continue
            request=prompts.verification_request(ops.prepare_verification(book,doc,plan).model_view())
            wire=client.prepare_request(request,p.raster);measured.append(wire.bound_usd)
            assert wire.bound_usd<=quote['verifier_bound_usd']
            assert wire.response_token_bound<=quote['verifier_response_token_bound']
    assert max(measured)==quote['verifier_bound_usd']
    proposer=typed_model.TypedStageClient('', 'proposer').prepare_request(prompts.operation_request(p.model_view()),p.raster)
    assert proposer.sha256==quote['proposer_request_sha256']
    assert proposer.bound_usd==quote['proposer_bound_usd']


def test_quote_uses_full_current_source_and_never_dispatches(source,monkeypatch):
    from cps.services.reflow.structural_quote import measure
    from cps.services.reflow import model
    def forbidden(*a,**kw):raise AssertionError('a quote must not access the provider')
    monkeypatch.setattr(model.requests,'get',forbidden);monkeypatch.setattr(model.requests,'post',forbidden)
    _,doc,_=source
    quote=measure(doc,prepared_result=prepared_result(source))
    assert quote['source_context_pages']==1 and quote['eligible_pages']==1
    assert quote['full_bound_usd']==sum(p['proposer_bound_usd']+p['verifier_bound_usd'] for p in quote['pages'])
    assert quote['full_bound_usd']>quote['proposer_bound_usd']>0
    assert quote['confirmed_usd']==0 and quote['held_usd']==0
    assert quote['source_sha256']==quote['pages'][0]['source_sha256']
    assert quote['kind']=='reservation_ceiling_not_expected_bill'


def test_maximum_nonoverlap_keeps_sixty_four_disjoint_choices_and_avoids_long_overlap():
    from cps.services.reflow.structural_quote import maximum_legal_ids
    rows=[dict(candidate_id=f'op-{i:024d}',element_id='e0',source_range=[i*2,i*2+1]) for i in range(64)]
    assert len(maximum_legal_ids(rows))==64
    rows[-1]['source_range']=[0,1000]
    assert len(maximum_legal_ids(rows))==63


def test_sixty_four_choice_protocol_fits_actual_both_stage_wire_allowance(source):
    import base64,hashlib,json
    from tests.unit.test_reflow_operation_prompt import view
    p=prepared(source);v=view(64)
    v['source_image']={'sha256':hashlib.sha256(p.raster).hexdigest(),'data_url':'data:image/jpeg;base64,'+base64.b64encode(p.raster).decode()}
    ids=[c['candidate_id'] for c in v['candidates']]
    proposal=prompts.operation_request(v)
    v['verification']={'protocol':ops.VERIFICATION_PROTOCOL,'proposal_id':ops.proposal_identity(v['snapshot_id'],ids),'proposed_ids':ids}
    approval=prompts.verification_request(v)
    for stage,request,key in [('proposer',proposal,'select'),('verifier',approval,'approve')]:
        wire=typed_model.TypedStageClient('',stage).prepare_request(request,p.raster)
        payload=json.loads(wire.payload_json)
        assert wire.response_token_bound<=payload['max_tokens']==4096
        content=json.loads(payload['messages'][1]['content'][1]['text'])
        complete=dict(content['empty_response'],**{key:ids})
        assert len(json.dumps(complete).encode())<wire.response_token_bound
        assert wire.bound_usd>0


def test_stale_consent_stops_before_dispatch_but_retains_complete_source(source):
    from cps.services.reflow.structural_quote import measure, consent_observer
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    from cps.services.reflow.ledger import Ledger
    from cps.services.reflow.pipeline import PageCache
    from tests.unit.test_reflow_structural_pipeline import WorkflowSession
    book,doc,tmp=source
    quote=measure(doc,prepared_result=prepared_result(source))
    quote['pages'][0]['proposer_request_sha256']='0'*64
    session=WorkflowSession()
    result=run_structural(doc,client=TwoStageClient('inert',enabled=True,session=session),
        ledger=Ledger(str(tmp/'ledger'),cap_usd=1),cache=PageCache(tmp/'cache'),
        prepared_result=prepared_result(source),prepared_observer=consent_observer(quote,doc))
    assert session.calls==[] and not result.operation_plans
    assert result.stopped=='estimate_stale' and result.structural['unreviewed']==1
    assert set(result.page_html)==set(book.pages) and result.source_pages[0].html==result.page_html[0]


def test_current_consent_allows_actual_two_stage_adoption(source):
    from cps.services.reflow.structural_quote import measure,consent_observer
    from cps.services.reflow.structural_pipeline import run_structural,TwoStageClient
    from cps.services.reflow.ledger import Ledger
    from cps.services.reflow.pipeline import PageCache
    from tests.unit.test_reflow_structural_pipeline import WorkflowSession
    _,doc,tmp=source;quote=measure(doc,prepared_result=prepared_result(source));session=WorkflowSession()
    result=run_structural(doc,client=TwoStageClient('inert',enabled=True,session=session),
        ledger=Ledger(str(tmp/'ledger'),cap_usd=1),cache=PageCache(tmp/'cache'),
        prepared_result=prepared_result(source),prepared_observer=consent_observer(quote,doc))
    assert len(session.calls)==2 and result.structural['approved_operations']==1
