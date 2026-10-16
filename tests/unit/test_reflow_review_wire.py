"""All source-bound stages share one exact lossless transport contract."""
import copy
import json
import pytest
from cps.services.reflow import layout_model as lm, layout_pipeline, layout_wire as codec
from cps.services.reflow.typed_model import _encoded
from tests.unit.test_reflow_layout_transport import envelope, Session
pytestmark=pytest.mark.unit


def large_view():
    # Typed values and absent keys must remain distinct, including protected
    # atoms, exact page geometry, operations and Unicode in neighboring text.
    rows=[dict(id=f'a{i}',text='  e\u0301—α 😀 \\ "\n'+str(i),protected=i%2==0,
               bbox=[1.234567890123,2,3.0,4],kind='paragraph',metadata={'locked':True})
          for i in range(260)]
    rows[0].update(optional=None,typed=False)
    rows[1].update(typed=0)
    rows[2].update(typed=0.0)
    return dict(atoms=rows,previous={'atoms':copy.deepcopy(rows[-120:])},
                proposed_layout=[dict(role='paragraph',ranges=[[a['id'],a['id']]],text=a['text']) for a in rows])


@pytest.mark.parametrize('stage',['proposer','reviewer','ordering','lexical'])
def test_every_source_bound_stage_admits_exact_large_view_and_keeps_schema(stage):
    client=layout_pipeline.LayoutClient('').stages[stage]
    req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review every exact value.'))
    logical=large_view();req['messages'][-1]['content']=json.dumps(logical,ensure_ascii=False)
    untouched=copy.deepcopy(req)
    wire=client.prepare_request(req)
    assert _encoded(codec.source_view(wire.payload['messages']))==_encoded(logical)
    assert req==untouched and wire.payload['response_format']['json_schema']['schema']==req['response_schema']
    client._check_request(wire)
    assert len(_encoded(wire.payload['messages']))+len(_encoded(req['response_schema']))<=48000
    assert wire.bound_usd==(wire.prompt_tokens_bound*client.reservation_rates[0]*1.25+wire.response_token_bound*client.reservation_rates[1])/1e6


@pytest.mark.parametrize('stage',['reviewer','ordering','lexical'])
def test_corrupt_encoder_refuses_before_returning_a_request_or_posting(monkeypatch,stage):
    client=layout_pipeline.LayoutClient('').stages[stage]
    session=Session(stage);client._session=session
    req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review every exact value.'))
    req['messages'][-1]['content']=json.dumps(large_view(),ensure_ascii=False)
    untouched=copy.deepcopy(req);real=codec.pack
    def corrupt(view):
        packed=real(view);packed['view']={'words':'changed'};return packed
    monkeypatch.setattr(codec,'pack',corrupt)
    with pytest.raises(ValueError,match='changed logical source'):client.prepare_request(req)
    assert req==untouched and session.posts==[]


def test_historical_wires_are_readable_and_corrupt_references_refuse_even_below_ceiling():
    client=layout_pipeline.LayoutClient('').stages['reviewer'];req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review every exact value.'))
    for version,definition in [(codec.LEGACY_VERSION,codec.LEGACY_DEFINITION),(codec.VERSION,codec.DEFINITION)]:
        packed=dict(wire_format=version,definition=definition,value_pool=['exact'],view={codec.KEY:dict(fields=['text'],rows=[[{'$v':0}]])})
        req['messages'][-1]['content']=_encoded(packed).decode()
        wire=client.prepare_request(req)
        assert codec.source_view(wire.payload['messages'])==[{'text':'exact'}]
        client._check_request(wire)
        for reference in (True,-1,1,0.0):
            broken=copy.deepcopy(packed);broken['view'][codec.KEY]['rows'][0][0]={'$v':reference}
            req['messages'][-1]['content']=_encoded(broken).decode()
            with pytest.raises(ValueError,match='reference'):client.prepare_request(req)


def test_no_truncation_or_larger_ceiling_for_uncompressible_review():
    client=layout_pipeline.LayoutClient('').stages['reviewer'];req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review every exact value.'))
    req['messages'][-1]['content']=json.dumps({'text':'x'*50000})
    with pytest.raises(ValueError,match='48000'):client.prepare_request(req)
    # Generic non-source protocols do not acquire model-side decoding rules.
    req['protocol']='unrelated';req['messages'][-1]['content']=json.dumps(large_view())
    with pytest.raises(ValueError,match='48000'):client.prepare_request(req)


def test_column_dictionaries_and_shared_compounds_expand_typed_values_exactly():
    # Independent protocol example: integer cells select values, so false, zero
    # and 0.0 are distinct, and absent keys cannot be filled by sharing.
    packed=dict(wire_format=codec.VERSION,definition=codec.DEFINITION,
        value_pool=[[1.123456789012345,2,3.0,4],{'bbox':{'$v':0},'text':'e\u0301 😀'}],
        view={codec.KEY:dict(fields=['id','typed','geometry'],column_values={'typed':[False,0,0.0,None]},
             rows=[['a0',0,{'$v':1}],['a1',1,{'$v':1},{'optional':None}],['a2',2,{'$v':1}],['a3',3,{'$v':1}]])})
    expected=[dict(id=f'a{i}',typed=value,geometry={'bbox':[1.123456789012345,2,3.0,4],'text':'e\u0301 😀'}) for i,value in enumerate([False,0,0.0,None])]
    expected[1]['optional']=None
    assert _encoded(codec.unpack(packed))==_encoded(expected)
    assert packed['view'][codec.KEY]['rows'][0][1]==0
    assert _encoded(codec.unpack(codec.pack(expected*80)))==_encoded(expected*80)


@pytest.mark.parametrize('corruption',['boolean_column','negative_column','out_of_range_column','unknown_column','empty_column','forward_pool','cycle_pool','unused_cycle','bad_pool_type'])
def test_malformed_new_representation_refuses_through_actual_prepare(corruption):
    packed=dict(wire_format=codec.VERSION,definition=codec.DEFINITION,
        value_pool=['exact'],view={codec.KEY:dict(fields=['text'],column_values={'text':['exact']},rows=[[0]])})
    table=packed['view'][codec.KEY]
    if corruption=='boolean_column':table['rows'][0][0]=True
    elif corruption=='negative_column':table['rows'][0][0]=-1
    elif corruption=='out_of_range_column':table['rows'][0][0]=1
    elif corruption=='unknown_column':table['column_values']['absent']=['wrong']
    elif corruption=='empty_column':table['column_values']['text']=[]
    elif corruption=='forward_pool':packed.update(value_pool=[{'$v':1},'wrong'],view={'$v':0})
    elif corruption=='cycle_pool':packed.update(value_pool=[{'$v':0}],view={'$v':0})
    elif corruption=='unused_cycle':packed['value_pool']=[{'$v':0}]
    else:packed['value_pool']=[True]
    req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review.'));req['messages'][-1]['content']=_encoded(packed).decode()
    with pytest.raises(ValueError):layout_pipeline.LayoutClient('').stages['reviewer'].prepare_request(req)


def test_legacy_two_geometry_defaults_and_absence_are_still_exact():
    packed=dict(wire_format=codec.TABLE2_VERSION,definition=codec.TABLE2_DEFINITION,value_pool=[[1.25,2,3,4]],
        view={codec.KEY:dict(fields=['id'],defaults={'protected':True,'bbox':{'$v':0}},rows=[['a0'],['a1',{'protected':False,'metadata':None}]])})
    assert codec.unpack(packed)==[dict(id='a0',protected=True,bbox=[1.25,2,3,4]),dict(id='a1',protected=False,bbox=[1.25,2,3,4],metadata=None)]
    damaged=copy.deepcopy(packed);damaged['view'][codec.KEY]['column_values']={'id':['a0']}
    with pytest.raises(ValueError):codec.unpack(damaged)


def test_tampered_wire_refuses_before_context_lookup_reservation_or_post(tmp_path):
    from dataclasses import replace
    from cps.services.reflow.ledger import Ledger
    session=Session('reviewer')
    session.get=lambda *a,**k:(_ for _ in ()).throw(AssertionError('context lookup before integrity gate'))
    client=lm.LayoutStageClient('fixture','reviewer',session=session)
    req=envelope();req['protocol']='source-bound-layout-1';req['messages'].insert(0,dict(role='system',content='Review every source value.'))
    req['messages'][-1]['content']=json.dumps(large_view(),ensure_ascii=False)
    wire=client.prepare_request(req)
    payload=copy.deepcopy(wire.payload)
    logical=codec.source_view(payload['messages']);logical['atoms'][0]['bbox'][0]+=0.000000001
    payload['messages'][-1]['content']=_encoded(codec.pack(logical)).decode()
    changed=replace(wire,payload_json=_encoded(payload).decode())
    ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    with pytest.raises(ValueError,match='identity or reservation'):client.call(changed,ledger=ledger)
    assert ledger.entries('reservation')==[] and ledger.pending_usd()==0 and session.posts==[]
