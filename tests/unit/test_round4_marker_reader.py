"""Independent marker transport controls; all sockets mocked, no paid reads."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
import time
import pytest
import requests
from PIL import Image
from cps.services.reflow import vision_reading
from cps.services.reflow.ledger import Ledger, CapExceeded


def jpeg():
    stream = io.BytesIO()
    Image.new('RGB', (80, 60), 'white').save(stream, 'JPEG')
    return stream.getvalue()


def reply(answer=None, finish='stop', actual_model=None):
    class Reply:
        def raise_for_status(self):
            pass
        def json(self):
            return {'id': 'synthetic-marker-receipt', 'model': actual_model or vision_reading.MODEL,
                    'usage': {'cost': .00004}, 'choices': [{'finish_reason': finish,
                    'message': {'content': json.dumps(answer if answer is not None else {'label':'17','uncertain':False})}}]}
    return Reply()


def test_marker_cache_dispatches_once_with_durable_liability_and_strict_route(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.jsonl'
    calls = []
    def post(*a, **k):
        assert Ledger(path, .05).pending_usd() > 0, 'hold must exist before dispatch'
        calls.append(k['json'])
        return reply()
    monkeypatch.setattr(requests, 'post', post)
    first = vision_reading.Reader('inert-key', tmp_path/'cache', Ledger(path,.05)).read_marker(jpeg())
    last = vision_reading.Reader(None, tmp_path/'cache', Ledger(path,.05)).read_marker(jpeg())
    assert first['label']=='17' and first['uncertain'] is False and first['cached'] is False
    assert last['cached'] is True and last['new_cost_usd']==0 and last['label']=='17'
    assert len(calls)==1 and Ledger(path,.05).spent()==pytest.approx(.00004)
    assert Ledger(path,.05).pending_usd()==0
    wire = calls[0]
    assert wire['reasoning']['effort']=='low' and wire['provider']['allow_fallbacks'] is False
    assert wire['provider']['order']==['openai']
    schema = wire['response_format']['json_schema']
    assert schema['strict'] is True
    assert set(schema['schema']['required'])=={'label','uncertain'}
    assert schema['schema']['additionalProperties'] is False


def test_marker_cap_refuses_before_dispatch(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(requests,'post',lambda *a,**k:calls.append(k))
    reader=vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(tmp_path/'ledger',.000001))
    with pytest.raises(CapExceeded):reader.read_marker(jpeg())
    assert calls==[]


def test_missing_offline_marker_cache_never_reserves_or_dispatches(tmp_path, monkeypatch):
    calls=[];path=tmp_path/'ledger'
    monkeypatch.setattr(requests,'post',lambda *a,**k:calls.append(k))
    reader=vision_reading.Reader(None,tmp_path/'cache',Ledger(path,.05))
    with pytest.raises(vision_reading.model.ModelError):reader.read_marker(jpeg())
    assert calls==[] and Ledger(path,.05).pending_usd()==0 and Ledger(path,.05).spent()==0


@pytest.mark.parametrize('failure',['timeout','truncated','invalid-label','missing-flag','string-flag','extra-field','wrong-model'])
def test_unusable_marker_keeps_liability_and_never_retries(tmp_path, monkeypatch, failure):
    path=tmp_path/'ledger';calls=[]
    def post(*a,**k):
        calls.append(True)
        if failure=='timeout':raise requests.Timeout('synthetic lost marker response')
        value={'label':'17','uncertain':False}
        if failure=='invalid-label':value['label']='An invented prose explanation'
        if failure=='missing-flag':value.pop('uncertain')
        if failure=='string-flag':value['uncertain']='false'
        if failure=='extra-field':value['prose']='extra'
        return reply(value,finish='length' if failure=='truncated' else 'stop', actual_model='another/model' if failure=='wrong-model' else None)
    monkeypatch.setattr(requests,'post',post)
    first=vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(path,.05))
    with pytest.raises((requests.Timeout,vision_reading.model.ModelError,ValueError)):first.read_marker(jpeg())
    ledger=Ledger(path,.05)
    if failure=='timeout':assert ledger.pending_usd()>0 and ledger.spent()==0
    else:assert ledger.spent()==pytest.approx(.00004)
    again=vision_reading.Reader('inert-key',tmp_path/'cache',ledger)
    with pytest.raises(vision_reading.model.UncertainBilling):again.read_marker(jpeg())
    assert len(calls)==1


def test_uncertain_marker_response_preserves_uncertainty_in_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(requests,'post',lambda *a,**k:reply({'label':'','uncertain':True}))
    reader=vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(tmp_path/'ledger',.05))
    first=reader.read_marker(jpeg());last=reader.read_marker(jpeg())
    assert first['uncertain'] is last['uncertain'] is True
    assert first['label']==last['label']=='' and last['new_cost_usd']==0


def test_two_independent_readers_share_one_marker_dispatch(tmp_path, monkeypatch):
    calls=[];path=tmp_path/'ledger'
    def post(*a,**k):
        calls.append(True);time.sleep(.03);return reply()
    monkeypatch.setattr(requests,'post',post)
    readers=[vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(path,.05)) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda r:r.read_marker(jpeg()),readers))
    assert len(calls)==1 and sorted(r['cached'] for r in result)==[False,True]
    assert Ledger(path,.05).spent()==pytest.approx(.00004)


def test_marker_wire_cache_cannot_collide_with_ordinary_page_reading(tmp_path, monkeypatch):
    calls=[]
    def post(*a,**k):
        calls.append(k['json'])
        if len(calls)==1:return reply()
        return reply({'blocks':[{'id':'body','role':'body','bbox':[50,50,950,800],
            'text':'A wholly synthetic complete paragraph.','low_confidence':[],
            'marker':'','continues_previous':False,'continues_next':False}]})
    monkeypatch.setattr(requests,'post',post)
    reader=vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(tmp_path/'ledger',.05))
    prompt,schema=reader.prompt,json.dumps(reader.api_schema,sort_keys=True)
    marker=reader.read_marker(jpeg());page=reader.read(jpeg())
    assert len(calls)==2 and marker['config_sha256']!=page['config_sha256']
    assert page['answer']['blocks'][0]['text']=='A wholly synthetic complete paragraph.'
    assert reader.prompt==prompt and json.dumps(reader.api_schema,sort_keys=True)==schema
    assert reader.read_marker(jpeg())['cached'] and reader.read(jpeg())['cached']
    assert len(calls)==2
