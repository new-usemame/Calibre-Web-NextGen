"""Typed stages share durable transport but never the whole-HTML parser."""
import json
import hashlib
import base64
import pytest
import pymupdf
import requests
from cps.services.reflow import model, prompts, structural_ops as ops
from cps.services.reflow.ledger import Ledger

pytestmark=pytest.mark.unit


def request():
    doc=pymupdf.open();page=doc.new_page(width=200,height=200);page.insert_text((20,40),'Original')
    raster=page.get_pixmap().tobytes('jpeg');doc.close()
    view={'protocol':ops.PROTOCOL,'snapshot_id':'a'*64,'source_revision':'test',
          'source_pdf_sha256':'b'*64,'page_index0':0,'context':{},'coverage':{},
          'candidates':[{'candidate_id':'op-one','element_id':'e0','kind':'heading','source_range':[0,8]}],
          'source_image':{'sha256':hashlib.sha256(raster).hexdigest(),'data_url':'data:image/jpeg;base64,'+base64.b64encode(raster).decode()}}
    return prompts.operation_request(view),raster


class Session:
    def __init__(self, data=None, error=None, status=200, route=None):self.data=data;self.error=error;self.status=status;self.calls=[];self.route=route
    def get(self,url,**kwargs):
        verifier='terra' in url
        root={'architecture':{'input_modalities':['image','text']},'endpoints':[{
            'tag':'openai/flex','status':0,'max_prompt_tokens':200000,'max_completion_tokens':8192,
            'supported_parameters':['reasoning','max_tokens'],
            'pricing':{'prompt':str(1e-6 if verifier else 1e-7),'completion':str(6e-6 if verifier else 6e-7)}}]}
        return Session({'data':self.route if self.route is not None else root}).post()
    def post(self,*args,**kwargs):
        self.calls.append(kwargs)
        if self.error:raise self.error
        parent=self
        class Response:
            status_code=parent.status;headers={};text='SECRET RAW PROVIDER BODY'
            def json(self):return parent.data
        return Response()


def reply(content=None):
    return {'model':'openai/gpt-5.6-luna','provider':'OpenAI','service_tier':'flex','id':'gen-test',
        'usage':{'prompt_tokens':1000,'completion_tokens':100,'cost':0.000123456789},
        'choices':[{'finish_reason':'stop','message':{'content':content or json.dumps({'protocol':ops.PROTOCOL,'snapshot_id':'a'*64,'select':[]})}}]}


def test_typed_wire_bound_and_settlement_are_exact_and_stage_bound(tmp_path):
    from cps.services.reflow.typed_model import TypedStageClient
    req,raster=request();session=Session(reply());ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    client=TypedStageClient('test-key','proposer',session=session)
    wire=client.prepare_request(req,raster)
    assert wire.payload['provider']['order']==['openai/flex']
    assert wire.payload['provider']['allow_fallbacks'] is False
    assert wire.payload['service_tier']=='flex' and wire.payload['reasoning']=={'effort':'medium'}
    assert wire.payload['max_tokens']==4096
    assert wire.payload['messages'][1]['content'][0]['image_url']['detail']=='original'
    assert wire.bound_usd>0 and wire.response_token_bound<=4096
    answer=client.call(wire,ledger=ledger,page_label='0')
    assert answer.response['select']==[]
    assert ledger.spent()==0.000123456789
    assert ledger.pending_usd()==0
    pending=ledger.entries('reservation')[0]
    assert pending['stage']=='proposer' and pending['request_sha256']==wire.sha256
    assert pending['prompt_version']==req['prompt_version']
    assert 'json' not in session.calls[0]
    assert session.calls[0]['data']==wire.payload_json.encode('utf-8')
    assert hashlib.sha256(session.calls[0]['data']).hexdigest()==wire.sha256
    ledger.record({'kind':'attempt_diagnostic','attempt':answer.attempt,'reason':'example','cost_usd':0})
    assert Ledger(ledger.path,cap_usd=1).spent()==0.000123456789


@pytest.mark.parametrize('failure',['malformed','length','offtier','missingcost','nan','negative','timeout','http'])
def test_typed_failure_never_becomes_free_or_leaks_provider_text(tmp_path,failure):
    from cps.services.reflow.typed_model import TypedStageClient
    data=reply();error=None;status=200
    if failure=='malformed':data['choices'][0]['message']['content']='not JSON'
    elif failure=='length':data['choices'][0]['finish_reason']='length'
    elif failure=='offtier':data['service_tier']='default'
    elif failure=='missingcost':del data['usage']['cost']
    elif failure=='nan':data['usage']['cost']=float('nan')
    elif failure=='negative':data['usage']['cost']=-1
    elif failure=='timeout':error=requests.ReadTimeout('SECRET RAW PROVIDER BODY')
    elif failure=='http':status=503;data={'error':{'message':'SECRET RAW PROVIDER BODY','code':503}}
    req,raster=request();session=Session(data,error,status);ledger=Ledger(str(tmp_path/'ledger'),cap_usd=1)
    client=TypedStageClient('test-key','proposer',session=session);wire=client.prepare_request(req,raster)
    with pytest.raises(model.ModelError) as caught:client.call(wire,ledger=ledger,page_label='0')
    assert 'SECRET' not in str(caught.value) and 'SECRET' not in (tmp_path/'ledger').read_text()
    assert len(session.calls)==1
    if failure in ['malformed','length','offtier']:
        assert ledger.spent()==0.000123456789 and ledger.pending_usd()==0
    else:assert ledger.spent()==0 and ledger.pending_usd()==wire.bound_usd
    assert ledger.entries('attempt_diagnostic')


def test_parallel_shared_request_claim_has_one_owner_and_no_cross_user_details(tmp_path):
    from cps.services.reflow.operation_cache import OperationCache,PriorRequestPending
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    cache=OperationCache(tmp_path);barrier=Barrier(2);key='a'*64
    def acquire():
        barrier.wait()
        try:return cache.claim(key)
        except PriorRequestPending:return 'pending'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:acquire(),range(2)))
    assert results.count('pending')==1
    token=next(r[0] for r in results if r!='pending')
    cache.finish(key,token,response={'select':[]})
    new_owner,cached=OperationCache(tmp_path).claim(key)
    assert new_owner is None and cached=={'state':'answered','request_sha256':key,'response':{'select':[]}}
    assert not any(word in json.dumps(cached) for word in ['user','job','cost','ledger'])


def test_shared_pending_survives_restart_and_known_unsent_claim_can_be_released(tmp_path):
    from cps.services.reflow.operation_cache import OperationCache,PriorRequestPending
    cache=OperationCache(tmp_path);key='b'*64;token,_=cache.claim(key)
    with pytest.raises(PriorRequestPending):OperationCache(tmp_path).claim(key)
    cache.release_unsent(key,token)
    token,_=cache.claim(key);cache.finish(key,token,rejected=True)
    assert cache.claim(key)[1]['state']=='rejected'


def test_unavailable_route_blocks_before_any_reservation_or_paid_post(tmp_path):
    from cps.services.reflow.typed_model import TypedStageClient
    session=Session(reply(),route={'architecture':{'input_modalities':['text']},'endpoints':[]})
    client=TypedStageClient('test','proposer',session=session);req,raster=request()
    ledger=Ledger(str(tmp_path/'no-route'),cap_usd=1)
    with pytest.raises(model.ModelError):client.call(client.prepare_request(req,raster),ledger=ledger)
    assert session.calls==[] and ledger.entries()==[]


def test_request_cannot_be_retargeted_or_underreserved_after_preparation(tmp_path):
    from cps.services.reflow.typed_model import TypedStageClient
    from dataclasses import replace
    session=Session(reply());client=TypedStageClient('test','proposer',session=session);req,raster=request()
    wire=client.prepare_request(req,raster)
    with pytest.raises(ValueError):client.call(replace(wire,bound_usd=0),ledger=Ledger(str(tmp_path/'tamper'),cap_usd=1))
    assert session.calls==[]
