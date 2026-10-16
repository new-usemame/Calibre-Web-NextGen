"""Complete decision capacity and exact source representation at transport seams."""
import copy,json
from dataclasses import replace
import pytest
from cps.services.reflow import layout_model as lm,layout_pipeline,layout_quote,model
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_layout_transport import source_atom_envelope,Session,envelope
from tests.unit.test_reflow_layout_ops import boundary_source
pytestmark=pytest.mark.unit


def test_complete_fragmented_proposal_has_visible_and_supporting_space_and_reserved_cost():
    req,source=source_atom_envelope(700)
    client=layout_pipeline.LayoutClient('').stages['proposer']
    wire=client.prepare_request(req)
    assert wire.payload['reasoning']=={'effort':'medium'}
    # One atom per group bounds maximal fragmentation. Longest roles and joins
    # deliberately overapproximate legal outputs; this is not a candidate plan.
    shape={'snapshot':'s'*64,'groups':[{'role':'source_furniture','ranges':[[a['id'],a['id']]]} for a in source['atoms']],
        'joins':[{'left':a['id'],'right':a['id'],'hyphen':'keep'} for a in source['atoms']],
        'emphasis':[[a['id'],a['id']] for a in source['atoms']],
        'continuation':False,'boundary_join':None}
    assert wire.response_token_bound >= len(lm._encoded(shape))+1024+16384
    assert wire.response_token_bound==wire.payload['max_tokens']
    assert wire.bound_usd==(wire.prompt_tokens_bound*.10*1.25+wire.response_token_bound*.375)/1e6
    client._check_request(wire)


def test_lossless_table_view_preserves_exact_absence_unicode_geometry_and_neighbor():
    from cps.services.reflow.layout_wire import pack,unpack
    req,view=source_atom_envelope(700)
    view['atoms'][1]['geometry']=[1.234567890123,2,3,4]
    view['atoms'][2]['text']='  e\u0301—α 😀 \\ "\n'
    view['atoms'][3]['extra']=None
    view['printed_lines']=[dict(source_line=i,bbox=[i+.123456789,2,3,4],ranges=[[f'a{i}',f'a{i}']]) for i in range(100)]
    before=copy.deepcopy(view);packed=pack(view)
    assert len(lm._encoded(packed)) < len(lm._encoded(view))
    assert unpack(packed)==before and view==before
    assert unpack(packed)['atoms'][2]['text'].encode()==before['atoms'][2]['text'].encode()
    assert 'extra' not in unpack(packed)['atoms'][1]
    with pytest.raises(ValueError):unpack({'wire_format':'layout-table-1','view':{'$layout_table':{'fields':['x','x'],'rows':[[1,2,{}]]}}})


def test_quote_and_cache_bind_actual_reasoning_and_dynamic_completion(monkeypatch):
    versions=layout_quote._versions(); req,_=source_atom_envelope(700)
    client=layout_pipeline.LayoutClient('').stages['proposer'];wire=client.prepare_request(req)
    monkeypatch.setitem(lm.PROFILES,'proposer',replace(lm.PROFILES['proposer'],reasoning_effort='low'))
    assert layout_quote._versions()!=versions
    changed=layout_pipeline.LayoutClient('').stages['proposer'].prepare_request(req)
    assert changed.sha256!=wire.sha256
    with pytest.raises(ValueError):layout_pipeline.LayoutClient('').stages['proposer']._check_request(wire)
    fake=dict(versions,identity=layout_quote._digest(versions))
    with pytest.raises(EstimateStale):layout_quote._current_quote(fake)


def test_under_cap_route_admits_dynamic_wire_but_insufficient_capacity_refuses_before_post():
    session=Session();session.route['max_completion_tokens']=128000
    client=layout_pipeline.LayoutClient('fixture',session=session).stages['proposer']
    req,_=source_atom_envelope(700);wire=client.prepare_request(req)
    client.preflight(wire.prompt_tokens_bound,wire.response_token_bound)
    session.route['max_completion_tokens']=wire.response_token_bound-1
    with pytest.raises(model.ModelError):client.call(wire)
    assert session.posts==[]


@pytest.mark.parametrize('failure,code',[('length','completion_limit'),('tool_calls','incomplete_response'),('malformed','malformed_json'),('reasoning','allocation_mismatch')])
def test_settlement_and_one_attempt_survive_every_completion_refusal(tmp_path,failure,code):
    from cps.services.reflow.ledger import Ledger
    session=Session();client=layout_pipeline.LayoutClient('fixture',session=session).stages['proposer']
    if failure=='reasoning':
        client=lm.LayoutStageClient('fixture','proposer',session=session,
            profile=replace(lm.PROFILES['proposer'],reasoning_effort='none',supporting_reasoning_tokens=0))
    if failure in ('length','tool_calls'):session.data['choices'][0]['finish_reason']=failure
    elif failure=='malformed':session.data['choices'][0]['message']['content']='{"items":['
    else:session.data['usage']['completion_tokens_details']={'reasoning_tokens':17}
    request=client.prepare_request(envelope());ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    with pytest.raises(model.ModelError) as exc:client.call(request,ledger=ledger)
    assert exc.value.reason_code==code
    assert ledger.spent()==.0001 and ledger.pending_usd()==0
    assert len(session.posts)==1
    if failure=='reasoning':assert exc.value.stop_dispatch


def test_exact_pool_reconstruction_and_invalid_references_refuse():
    from cps.services.reflow.layout_wire import pack,unpack
    box=[1.12345678901234,2.23456789012345,3.34567890123456,4.45678901234567]
    original={'lines':[{'id':str(i),'bbox':box,'text':'unchanged'} for i in range(20)]}
    packed=pack(original)
    assert unpack(packed)==original
    assert len(lm._encoded(packed))<len(lm._encoded(original))
    # Compression may share the rectangle as a column default or pool entry;
    # both must reconstruct every coordinate. Exercise pool integrity explicitly.
    packed=dict(packed,value_pool=[box],view={'$v':0})
    assert unpack(packed)==box
    damaged=copy.deepcopy(packed);damaged['view']={'$v':True}
    with pytest.raises(ValueError):unpack(damaged)
    damaged['view']={'$v':1}
    with pytest.raises(ValueError):unpack(damaged)


def test_expanded_allocation_is_reserved_before_dispatch_and_cap_is_not_silently_clamped(tmp_path):
    from cps.services.reflow.ledger import Ledger
    req,_=source_atom_envelope(700);session=Session();session.route['max_completion_tokens']=128000
    client=layout_pipeline.LayoutClient('fixture',session=session).stages['proposer'];wire=client.prepare_request(req)
    ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    session.on_post=lambda:(_ for _ in ()).throw(AssertionError('wrong reservation')) if ledger.pending_usd()!=wire.bound_usd else None
    client.call(wire,ledger=ledger)
    assert json.loads(session.posts[0]['data'])['max_tokens']==wire.response_token_bound>8192
    small=replace(lm.PROFILES['proposer'],completion_cap=8192)
    from cps.services.reflow.layout_allocation import completion_tokens
    with pytest.raises(ValueError,match='capacity'):completion_tokens('proposer',small,json.loads(req['messages'][-1]['content']))


def test_consent_quotes_complete_neighbor_candidate_context_and_refuses_changed_source(boundary_source):
    from cps.services.reflow import layout_ops,layout_requests
    from tests.unit.test_reflow_layout_ops import answer
    book,doc,sources,raws=boundary_source
    left=layout_ops.prepare(book,doc,0,sources[0],raws[0])
    plan=left.accept(book,doc,answer(left),source_page=sources[0],raw_page=raws[0])
    right=layout_ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    row=layout_quote.measure_page(book,doc,right,sources[1])
    body=dict(layout_quote._versions(),pages=[row],request_limits={s:layout_quote._maximum(s) for s in layout_quote._clients()})
    measured=dict(body,identity=layout_quote._digest(body))
    client=layout_pipeline.LayoutClient('').stages['proposer']
    request=layout_requests.proposal(right,plan)
    wire=client.prepare_request(request,right.raster)
    assert row['proposer_request_sha256']!=wire.sha256
    assert layout_quote.assert_request_bound(measured,'proposer',wire,1)
    view=json.loads(request['messages'][-1]['content']);view['atoms'][0]['text']+=' changed'
    request['messages'][-1]['content']=json.dumps(view)
    with pytest.raises(EstimateStale):layout_quote.assert_request_bound(measured,'proposer',client.prepare_request(request,right.raster),1)


def test_pooling_table_fields_and_numeric_rows_still_reconstructs_exactly():
    from cps.services.reflow.layout_wire import pack,unpack
    row={'a_long_mechanical_field_name':1.12345678901234,'b':2.23456789012345,'c':3.34567890123456,'d':4.45678901234567}
    value={str(i):[dict(row) for _ in range(10)] for i in range(5)}
    assert unpack(pack(value))==value
