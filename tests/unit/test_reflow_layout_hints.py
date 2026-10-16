"""Offline hint seams: issuance, full source/geometry, quotes and independent review."""
import copy
import json
from dataclasses import replace
import pytest
from cps.services.reflow import layout_hints as h, layout_requests as req, layout_pipeline as lp
from cps.services.reflow import layout_quote as q, layout_model as lm, layout_wire, model
from cps.services.reflow.structural_ops import ContractError
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_layout_ops import prepared,boundary_source
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result
from tests.unit.test_reflow_range_choices import range_fixture,verdict
pytestmark=pytest.mark.unit


@pytest.fixture
def hinted(source):
    book,doc,src,raw,p=prepared(source)
    wh=list(model._jpeg_dimensions(p.raster))
    pre=dict(original_raster_wh=wh,layout_wh=[1036,1036],processor_effective_wh=[1036,1036],
        layout_resize='PIL BICUBIC; anisotropic full-page stretch; no crop/padding/rotation',
        layout_scale_xy=[1036/wh[0],1036/wh[1]],processor_padding='token padding=True batch1; no pixel padding')
    # Synthetic overlapping declarations distinguish ambiguity retention from
    # choosing a best box. No inference or source-layout quality assertion.
    rawboxes=[dict(sequence=i,type='text',raw_bins=[0,0,1000,1000],angle=0,merge_prev_model_marker=False) for i in range(2)]
    receipt=dict(version=h.VERSION,model=h.MODEL,source=h.source_binding(p),
        declarations_sha256=h._hash(h._json(rawboxes).encode()),preprocessing_sha256=h._hash(h._json(pre).encode()))
    kw=dict(source_page=src,raw_page=raw)
    hint=h.prepare(book,doc,p,declarations=rawboxes,preprocessing=pre,receipt=receipt,**kw)
    return book,doc,src,raw,p,rawboxes,pre,receipt,kw,hint


def test_hinted_wire_is_bound_and_unhinted_and_review_remain_exact(hinted):
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    from cps.services.reflow import layout_ranges
    assert req.proposal(p)==layout_ranges.proposal(p)
    plain=req.proposal(p);en=req.proposal(p,hints=hint)
    client=lp.LayoutClient('',profile_selection=lm.DISABLED_SELECTION).stages['proposer']
    a=client.prepare_request(plain,p.raster);b=client.prepare_request(en,p.raster)
    assert a.sha256!=b.sha256 and b.response_token_bound==a.response_token_bound
    view=layout_wire.source_view(b.payload['messages'][:-1]);ad=view.pop('layout_advisory')
    assert view==json.loads(plain['messages'][-1]['content'])
    assert ad['multiple_overlap'] and all(row[1]==[0,1] for row in ad['lines'])
    assert b.payload['reasoning']=={'enabled':False}
    plan=p.accept(book,doc,range_fixture(p),prototype=True,**kw)
    review=req.review(p,plan)
    assert 'layout_advisory' not in json.loads(review['messages'][-1]['content'])
    assert 'layout_advisory' not in review['source_identity']
    assert req.validate_review(p,plan,verdict(plan)).accepted_plan
    # A saved object's bytes alone do not issue authority.
    with pytest.raises(ContractError):req.proposal(p,hints=h.LayoutHints(hint.packet_json))


def test_restoration_reconstructs_schema_all_relations_and_refuses_tamper(hinted):
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    body=json.loads(hint.packet_json)
    restored=h.prepare(book,doc,p,declarations=boxes,preprocessing=pre,receipt=receipt,packet=body,**kw)
    assert h.identity(p,restored)==h.identity(p,hint)
    for damage in ('relation','ambiguity','membership','id','raw','model','preprocessing','frame','extra'):
        bad=copy.deepcopy(body)
        if damage=='relation':bad['computed']['lines'][0]['relations']=[]
        elif damage=='ambiguity':bad['computed']['atoms'][0]['sequences']=[0]
        elif damage=='membership':bad['computed']['atoms'][0]['source_lines']=[]
        elif damage=='id':bad['computed']['atoms'][0]['id']='a9999'
        elif damage=='raw':bad['raw_declarations'][0]['raw_bins'][0]=1
        elif damage=='model':bad['receipt']['model']['model_revision']='forged'
        elif damage=='preprocessing':bad['preprocessing']['layout_wh']=[1000,1000]
        elif damage=='frame':bad['frame']['origin']=[1,0]
        else:bad['generated_words']='manual witness'
        with pytest.raises(ContractError):h.prepare(book,doc,p,declarations=boxes,preprocessing=pre,receipt=receipt,packet=bad,**kw)
    changed=copy.deepcopy(boxes);changed[0]['raw_bins'][0]=1
    with pytest.raises(ContractError):h.prepare(book,doc,p,declarations=changed,preprocessing=pre,receipt=receipt,**kw)
    for other in (replace(p,source_identity='changed'),replace(p,snapshot_id='f'*64),replace(p,raster=p.raster+b'x')):
        with pytest.raises(ContractError):req.proposal(other,hints=hint)
    object.__setattr__(hint,'packet_json',h._json(dict(body,computed={})))
    with pytest.raises(ContractError):req.proposal(p,hints=hint)


def test_quote_consent_and_request_cache_cannot_survive_hint_changes(hinted,source):
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    selected=lm.DISABLED_SELECTION
    plain=q.measure(doc,prepared_result=prepared_result(source),profile_selection=selected)
    quote=q.measure(doc,prepared_result=prepared_result(source),profile_selection=selected,layout_hint_packets={p.page:hint})
    assert quote['identity']!=plain['identity'] and quote['hint_quote_version']==h.QUOTE_VERSION
    client=lp.LayoutClient('',profile_selection=selected).stages['proposer']
    wire=client.prepare_request(req.proposal(p,hints=hint),p.raster)
    q.consent_observer(quote,doc,profile_selection=selected,layout_hint_packets={0:hint})(book,p,src)
    assert q.assert_request_bound(quote,'proposer',wire,0,profile_selection=selected)
    for other in (plain,):
        with pytest.raises(EstimateStale):q.assert_request_bound(other,'proposer',wire,0,profile_selection=selected)
    with pytest.raises(EstimateStale):q.consent_observer(quote,doc,profile_selection=selected)(book,p,src)
    changed=copy.deepcopy(boxes);changed[1]['type']='title'
    newreceipt=dict(receipt,declarations_sha256=h._hash(h._json(changed).encode()))
    newhint=h.prepare(book,doc,p,declarations=changed,preprocessing=pre,receipt=newreceipt,**kw)
    other=client.prepare_request(req.proposal(p,hints=newhint),p.raster)
    assert wire.sha256!=other.sha256
    with pytest.raises(EstimateStale):q.assert_request_bound(quote,'proposer',other,0,profile_selection=selected)
    with pytest.raises(EstimateStale):q.consent_observer(quote,doc,profile_selection=selected,layout_hint_packets={0:newhint})(book,p,src)
    assert quote['request_limits']==plain['request_limits']
    assert quote['pages'][0]['verifier_bound_usd']==plain['pages'][0]['verifier_bound_usd']
    assert quote['held_usd']==quote['confirmed_usd']==0


def test_generated_tail_and_manual_witness_never_enter_declarations(hinted):
    *_,boxes,pre,receipt,kw,hint=hinted
    supplied=[dict(r,tail='fabricated words',tail_sha256='untrusted',witness_range=['a0','a99']) for r in boxes]
    assert h.declarations(supplied)==boxes
    body=json.loads(hint.packet_json)
    assert set(body['raw_declarations'][0])=={'sequence','type','raw_bins','angle','merge_prev_model_marker'}


def test_neighbor_quote_binds_complete_instructions_and_envelope(boundary_source):
    from cps.services.reflow import layout_ops as ops
    book,doc,sources,raws=boundary_source
    left=ops.prepare(book,doc,0,sources[0],raws[0])
    previous=left.accept(book,doc,range_fixture(left),source_page=sources[0],raw_page=raws[0])
    p=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    wh=list(model._jpeg_dimensions(p.raster))
    pre=dict(original_raster_wh=wh,layout_wh=[1036,1036],processor_effective_wh=[1036,1036],layout_resize='PIL BICUBIC; anisotropic full-page stretch; no crop/padding/rotation',layout_scale_xy=[1036/wh[0],1036/wh[1]],processor_padding='token padding=True batch1; no pixel padding')
    boxes=[dict(sequence=0,type='text',raw_bins=[0,0,1000,1000],angle=0,merge_prev_model_marker=False)]
    receipt=dict(version=h.VERSION,model=h.MODEL,source=h.source_binding(p),declarations_sha256=h._hash(h._json(boxes).encode()),preprocessing_sha256=h._hash(h._json(pre).encode()))
    hint=h.prepare(book,doc,p,declarations=boxes,preprocessing=pre,receipt=receipt,source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0])
    selected=lm.DISABLED_SELECTION;clients=lp.LayoutClient('',profile_selection=selected).stages
    for packet in (None,hint):
        row=q.measure_page(book,doc,p,sources[1],profile_selection=selected,hints=packet)
        quote=dict(q._versions(selected,packet is not None),pages=[row],request_limits={s:q._maximum(s,c) for s,c in clients.items()})
        quote['identity']=q._digest(quote)
        en=req.proposal(p,previous,hints=packet)
        actual=clients['proposer'].prepare_request(en,p.raster)
        assert q.assert_request_bound(quote,'proposer',actual,1,profile_selection=selected)
        for change in ('extra_system','extra_user','version','context','protocol'):
            bad=copy.deepcopy(en)
            if change.startswith('extra'):bad['messages'].insert(1,dict(role='system' if change=='extra_system' else 'user',content='unquoted instruction'))
            elif change=='version':bad['prompt_version']='unquoted-version'
            elif change=='context':bad['context']['experiment']='unquoted'
            else:bad['protocol']='unquoted-protocol'
            wire=clients['proposer'].prepare_request(bad,p.raster)
            with pytest.raises(EstimateStale):q.assert_request_bound(quote,'proposer',wire,1,profile_selection=selected)
        with pytest.raises(EstimateStale):q.assert_request_bound(quote,'proposer',replace(actual,bound_usd=actual.bound_usd+.01),1,profile_selection=selected)


def test_limited_source_observer_does_not_swallow_invalid_or_changed_hints(hinted,source,monkeypatch):
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    selected=lm.DISABLED_SELECTION
    def limited(*args,**kwargs):raise ValueError('synthetic transport capacity wall')
    monkeypatch.setattr(q,'measure_page',limited)
    quote=q.measure(doc,prepared_result=prepared_result(source),profile_selection=selected,layout_hint_packets={0:hint})
    assert quote['limited_pages']==1 and quote['limited_sources'][0]['layout_advisory']==h.identity(p,hint)
    q.consent_observer(quote,doc,profile_selection=selected,layout_hint_packets={0:hint})(book,p,src)
    bad=h.LayoutHints(hint.packet_json)
    with pytest.raises(ContractError):q.consent_observer(quote,doc,profile_selection=selected,layout_hint_packets={0:bad})(book,p,src)
    changed=copy.deepcopy(boxes);changed[1]['type']='title'
    receipt=dict(receipt,declarations_sha256=h._hash(h._json(changed).encode()))
    other=h.prepare(book,doc,p,declarations=changed,preprocessing=pre,receipt=receipt,**kw)
    with pytest.raises(EstimateStale):q.consent_observer(quote,doc,profile_selection=selected,layout_hint_packets={0:other})(book,p,src)


def test_hinted_quote_keeps_full_original_independent_review_wire_authoritative(hinted,source):
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    selected=lm.DISABLED_SELECTION
    quote=q.measure(doc,prepared_result=prepared_result(source),profile_selection=selected,layout_hint_packets={0:hint})
    plan=p.accept(book,doc,range_fixture(p),prototype=True,**kw)
    en=req.review(p,plan);client=lp.LayoutClient('',profile_selection=selected).stages['reviewer']
    assert q.assert_request_bound(quote,'reviewer',client.prepare_request(en,p.raster),0,profile_selection=selected)
    for damage in ('source','hint','instruction','version','schema','channel'):
        bad=copy.deepcopy(en)
        if damage in ('source','hint'):
            v=json.loads(bad['messages'][-1]['content'])
            if damage=='source':v['atoms'][0]['text']='changed source'
            else:v['layout_advisory']={'invented':True}
            bad['messages'][-1]['content']=json.dumps(v)
        elif damage=='instruction':bad['messages'].insert(1,dict(role='system',content='unquoted review instruction'))
        elif damage=='version':bad['prompt_version']='unquoted'
        elif damage=='channel':bad['context']['decision_channel']='emphasis'
        else:bad['response_schema']['properties']['accept']={'type':'boolean','enum':[True]}
        wire=client.prepare_request(bad,p.raster)
        with pytest.raises(EstimateStale):q.assert_request_bound(quote,'reviewer',wire,0,profile_selection=selected)
    changed=range_fixture(p);changed['groups'][0]['role']='quote'
    other=p.accept(book,doc,changed,prototype=True,**kw)
    with pytest.raises(ContractError):req.validate_review(p,other,verdict(plan))


def test_opt_in_pipeline_uses_hints_and_required_structure_survives_style_refusal(hinted,source):
    from tests.unit.test_reflow_qualification_channels import Channels
    from cps.services.reflow import pipeline
    from cps.services.reflow.ledger import Ledger
    book,doc,src,raw,p,boxes,pre,receipt,kw,hint=hinted
    session=Channels(source,'semantic_rejection')
    quote=q.measure(doc,prepared_result=prepared_result(source),layout_hint_packets={0:hint})
    seen=[]
    def observe(stage,wire,page):
        assert q.assert_request_bound(quote,stage,wire,page)
        view=layout_wire.source_view(wire.payload['messages'][:-1])
        seen.append((stage,'layout_advisory' in view))
    tmp=source[2]
    result=lp.run_layout(doc,client=lp.LayoutClient('fixture',enabled=True,session=session),
        cache=pipeline.PageCache(tmp/'cache'),ledger=Ledger(str(tmp/'ledger'),cap_usd=1),
        prepared_result=prepared_result(source),layout_hint_packets={0:hint},
        prepared_observer=q.consent_observer(quote,doc,layout_hint_packets={0:hint}),request_observer=observe)
    assert seen==[('proposer',True),('reviewer',False),('reviewer',False)]
    assert len(result.layout_plans)==1 and result.structural['emphasis'][0]['status']=='rejected'
    assert result.structural['layout_hint_experiment'][0]==h.identity(p,hint)
