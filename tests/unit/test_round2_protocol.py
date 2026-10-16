"""Actual Reader wire/cache/ledger tests for token-coupled confidence."""
import copy
import io
import json
import requests
import pytest
from PIL import Image
from cps.services.reflow import vision_reading
from cps.services.reflow.ledger import Ledger


def image():
    b=io.BytesIO();Image.new('RGB',(90,120),'white').save(b,format='JPEG');return b.getvalue()


def block(tokens):
    return dict(id='b1',role='body',tokens=tokens,marker='',bbox=[10,10,990,900],continues_previous=False,continues_next=False)


def token(text,flag=False):return dict(text=text,low_confidence=flag)


def reply(answer):
    class R:
        def raise_for_status(self):pass
        def json(self):return dict(id='synthetic-coupled-confidence',model='openai/gpt-6-luna',usage={'cost':.0004},choices=[dict(finish_reason='stop',message={'content':json.dumps(answer)})])
    return R()


def reader(tmp_path,**kwargs):
    return vision_reading.Reader('inert-key',tmp_path/'cache',Ledger(tmp_path/'ledger.jsonl',.05),**kwargs)


def receive(tmp_path,monkeypatch,tokens):
    raw={'blocks':[block(tokens)]};saved=copy.deepcopy(raw);calls=[]
    def post(*a,**kw):
        assert Ledger(tmp_path/'ledger.jsonl',.05).pending_usd()>0
        calls.append(kw['json']);return reply(raw)
    monkeypatch.setattr(requests,'post',post)
    r=reader(tmp_path,confidence_protocol='tokens');first=r.read(image())
    second=reader(tmp_path,confidence_protocol='tokens').read(image())
    assert raw==saved,'Decoder must not rewrite raw paid evidence'
    assert len(calls)==1 and first['cached'] is False and second['cached'] is True
    assert second['new_cost_usd']==0 and Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0004)
    assert second['answer']==first['answer']
    return first,calls[0]


def test_wire_couples_every_confidence_flag_to_its_own_exact_token(tmp_path,monkeypatch):
    r,wire=receive(tmp_path,monkeypatch,[token('Echo',True),token('echo'),token('Echo'),token('7,'),token('bridge-'),token('side.')])
    b=r['answer']['blocks'][0]
    assert b['text']=='Echo echo Echo 7, bridge- side.' and b['low_confidence']==[0]
    fields=wire['response_format']['json_schema']['schema']['properties']['blocks']['items']['properties']
    assert 'tokens' in fields and 'text' not in fields and 'low_confidence' not in fields
    item=fields['tokens']['items'];assert set(item['required'])=={'text','low_confidence'}
    assert item['properties']['text']['type']=='string' and item['properties']['low_confidence']['type']=='boolean'
    assert r.get('confidence_tokens'),'Raw structurally coupled annotations must survive in the receipt'


@pytest.mark.parametrize('bad',[None,'false',0,1,[],{},'MISSING'])
def test_bad_or_missing_flag_preserves_page_and_marks_only_affected_word_uncertain(tmp_path,monkeypatch,bad):
    uncertain={'text':'quiet'} if bad=='MISSING' else token('quiet',bad)
    r,_=receive(tmp_path,monkeypatch,[token('Amber'),uncertain,token('birds.')])
    b=r['answer']['blocks'][0]
    assert b['text']=='Amber quiet birds.' and b['low_confidence']==[1]
    assert r.get('confidence_recovery'),'Conservative recovery must be explicit in the saved receipt'


@pytest.mark.parametrize('flag',[False,True,None])
def test_whitespace_item_preserves_every_word_and_cannot_fabricate_certainty(tmp_path,monkeypatch,flag):
    r,_=receive(tmp_path,monkeypatch,[token('Amber'),token('quiet silver',flag),token('birds.')])
    b=r['answer']['blocks'][0]
    assert b['text']=='Amber quiet silver birds.'
    # A schema-breaking token split requires uncertainty even if supplied false.
    assert b['low_confidence']==[1,2]
    assert r.get('confidence_recovery')


def test_attached_note_marker_does_not_shift_coupled_confidence_word_identity(tmp_path,monkeypatch):
    raw={'blocks':[block([token('Amber<n:n1>'),token('quiet',True),token('birds.')]),
                   dict(block([token('A'),token('printed'),token('note.')]),id='n1',role='footnote',marker='1',bbox=[10,910,990,990])]}
    monkeypatch.setattr(requests,'post',lambda *a,**kw:reply(raw))
    r=reader(tmp_path,confidence_protocol='tokens').read(image());b=r['answer']['blocks'][0]
    assert b['text']=='Amber<n:n1> quiet birds.' and b['low_confidence']==[1]
    aligned=vision_reading.align(b['text'],[],b['low_confidence'],has_layer=False)
    assert {aligned['tokens'][i] for i in aligned['marked']}=={'quiet'}


@pytest.mark.parametrize('badtext',[None,9,{},''])
def test_unusable_transcription_retains_paid_receipt_and_cannot_retry(tmp_path,monkeypatch,badtext):
    calls=[]
    def post(*a,**kw):calls.append(1);return reply({'blocks':[block([token('Amber'),token(badtext),token('birds.')])]})
    monkeypatch.setattr(requests,'post',post)
    r=reader(tmp_path,confidence_protocol='tokens')
    with pytest.raises((ValueError,vision_reading.model.UnusableAnswer)):r.read(image())
    assert Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0004)
    with pytest.raises(vision_reading.model.UncertainBilling):reader(tmp_path,confidence_protocol='tokens').read(image())
    assert calls==[1]


def test_coupled_mode_cache_is_separate_from_both_previous_confidence_protocols(tmp_path,monkeypatch):
    calls=[]
    def post(*a,**kw):
        fields=kw['json']['response_format']['json_schema']['schema']['properties']['blocks']['items']['properties'];calls.append(fields)
        if 'tokens' in fields:return reply({'blocks':[block([token('Amber'),token('birds.',True)])]})
        flags=['birds.'] if fields['low_confidence']['items']['type']=='string' else [1]
        b=block([]);b.pop('tokens');b.update(text='Amber birds.',low_confidence=flags)
        return reply({'blocks':[b]})
    monkeypatch.setattr(requests,'post',post)
    configurations=[{},dict(token_confidence=True),dict(confidence_protocol='tokens')]
    first=[reader(tmp_path,**kw).read(image()) for kw in configurations]
    again=[reader(tmp_path,**kw).read(image()) for kw in configurations]
    assert len(calls)==3 and all(not r['cached'] for r in first) and all(r['cached'] and r['new_cost_usd']==0 for r in again)
    assert Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0012)
    assert all(r['answer']['blocks'][0]['low_confidence']==[1] for r in first+again)


def test_conservative_flag_recovery_publishes_live_page_with_exact_uncertain_word_and_zero_cost_rebuild(tmp_path,monkeypatch):
    if __package__:
        from .test_round2_assembly import source,docs,live_paragraphs,NS
    else:
        from test_round2_assembly import source,docs,live_paragraphs,NS
    from cps.services.reflow import vision_pipeline,vision_pixels,build_epub
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:[])
    monkeypatch.setattr(vision_pipeline,'layer_words',lambda doc,n,display:[])
    calls=[];raw={'blocks':[block([token('Amber'),token('quiet','invalid'),token('birds.')])]}
    def post(*a,**kw):calls.append(1);return reply(raw)
    monkeypatch.setattr(requests,'post',post)
    document=source([[]]);out=tmp_path/'live.epub'
    try:
        first=vision_pipeline.convert(document,str(out),reader(tmp_path,confidence_protocol='tokens'))
        again=vision_pipeline.convert(document,str(tmp_path/'rebuilt.epub'),reader(tmp_path,confidence_protocol='tokens'))
    finally:document.close()
    assert build_epub.validate(str(out))==[] and live_paragraphs(docs(out))==['Amber quiet birds.']
    refs=[a for name,root in docs(out).items() if 'ch' in name for a in root.findall('.//x:a',NS) if a.get('class')=='source-disclosure-ref']
    assert len(refs)==1 and first['pages'][0]['blocks'][0]['marked']==[1]
    assert first['pages'][0].get('confidence_recovery') and again['new_cost_usd']==0
    assert len(calls)==1 and Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0004)


def test_undefined_note_identity_remains_unusable_despite_deferred_role_validation(tmp_path,monkeypatch):
    calls=[]
    def post(*a,**kw):calls.append(1);return reply({'blocks':[block([token('Amber<n:missing>'),token('birds.')])]})
    monkeypatch.setattr(requests,'post',post)
    with pytest.raises((ValueError,vision_reading.model.UnusableAnswer)):reader(tmp_path,confidence_protocol='tokens').read(image())
    with pytest.raises(vision_reading.model.UncertainBilling):reader(tmp_path,confidence_protocol='tokens').read(image())
    assert calls==[1] and Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0004)


@pytest.mark.parametrize('flags',[[],[1],None,[99],[True],['quiet'],'MISSING'])
def test_explicit_legacy_reuse_retains_original_cache_and_bill_and_never_invents_certainty(tmp_path,monkeypatch,flags):
    calls=[]
    b=block([]);b.pop('tokens');b.update(text='Amber quiet birds.',low_confidence=[1]);raw={'blocks':[b]}
    def post(*a,**kw):calls.append(1);return reply(raw)
    monkeypatch.setattr(requests,'post',post)
    old=reader(tmp_path).read(image());files=list((tmp_path/'cache').glob('*.json'));assert len(files)==1
    oldfile=files[0];stored=json.loads(oldfile.read_text())
    if flags=='MISSING':stored['answer']['blocks'][0].pop('low_confidence')
    else:stored['answer']['blocks'][0]['low_confidence']=flags
    oldfile.write_text(json.dumps(stored));original=oldfile.read_bytes()
    requested=reader(tmp_path,confidence_protocol='tokens',reuse_legacy=True)
    reused=requested.read(image());again=reader(tmp_path,confidence_protocol='tokens',reuse_legacy=True).read(image())
    valid=isinstance(flags,list) and all(type(x) is int and 0<=x<3 for x in flags)
    assert reused['answer']['blocks'][0]['text']=='Amber quiet birds.'
    assert reused['answer']['blocks'][0]['low_confidence']==(flags if valid else [0,1,2])
    assert reused['confidence_protocol']=='validated_legacy_cache'
    assert reused['config_sha256']==old['config_sha256'] and reused['requested_config_sha256']!=old['config_sha256']
    assert reused['cached'] and again['cached'] and reused['new_cost_usd']==again['new_cost_usd']==0
    if not valid:assert reused.get('confidence_recovery'),'Invalid legacy annotation recovery must remain visible'
    assert calls==[1] and oldfile.read_bytes()==original,'Explicit reuse cannot rebill or rewrite the old receipt'
    assert list((tmp_path/'cache').glob('*.json'))==files
    assert Ledger(tmp_path/'ledger.jsonl',.05).spent()==pytest.approx(.0004)
