"""Supervised source-image audit: actual child capture, never EPUB self-attestation."""
from types import SimpleNamespace
import zipfile
import pytest
from cps.services.reflow import operation_audit as audit, structural_ops as ops, extract, build_epub
from cps.services.reflow.native_ipc import NativeDocument
from tests.unit.test_reflow_operation_audit import image_quote, source
from tests.unit.test_reflow_task import rig, _ledger_rows

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def settle_prior_fixture_factories():
    # Synthetic cases deliberately repeat identical SourcePage values. Settle
    # prior fixture cycles before issuing their next weak factory authority.
    import gc
    gc.collect()


def remote_result(result, original, doc, raw=None):
    plan=result.operation_plans[0];p=plan.prepared;s=p.source_page
    prepared=ops.prepare(result.book,doc,p.page,p.revision,{'layer':'native'},
        source_page=s,raw_page=raw if raw is not None else extract.read_page(original,p.page))
    choice=next(c for c in prepared.candidates() if c['kind']=='quote')
    admitted=prepared.accept(result.book,doc,dict(protocol=ops.PROTOCOL,
        snapshot_id=prepared.snapshot_id,select=[choice['candidate_id']]),source_page=s)
    return SimpleNamespace(book=result.book,operation_plans=[admitted],stage_records=[])


def test_real_child_captures_selected_source_pixels(image_quote,tmp_path):
    result,original,_=image_quote
    with NativeDocument(original.name,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc)
        rows,matching=audit.capture(result,567,document=doc)
        built=build_epub.build(result.book,str(tmp_path/'actual.epub'),doc=doc,
            source_pages={0:result.operation_plans[0].prepared.source_page},operation_plans=result.operation_plans)
        assert audit.observe(built.path,rows,matching)[0]['status']=='verified'
        assert 'source_atoms_sha256' in rows[0]
        assert all(len(v)==64 for v in matching[rows[0]['candidate_id']]['resources'].values())


@pytest.mark.parametrize('mutation',['pixels','substitute','reorder','missing','role'])
def test_native_png_evidence_rejects_actual_artifact_changes(image_quote,tmp_path,mutation):
    import io
    import pymupdf
    from PIL import Image
    from xml.etree import ElementTree as ET
    result,original,_=image_quote
    raw=extract.read_page(original,0)
    pix=original[0].get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csGRAY)
    image=Image.frombytes('L',(pix.width,pix.height),pix.samples).convert('1')
    data=io.BytesIO();image.save(data,format='PNG')
    path=tmp_path/'bitonal.pdf'
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=original[0].rect.width,height=original[0].rect.height)
        page.insert_image(page.rect,stream=data.getvalue());pdf.save(path)
    with NativeDocument(path,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc,raw)
        rows,matching=audit.capture(result,567,document=doc)
        assert all(p.endswith('.png') for p in matching[rows[0]['candidate_id']]['resources'])
        built=build_epub.build(result.book,str(tmp_path/'png.epub'),doc=doc,
            source_pages={0:result.operation_plans[0].prepared.source_page},operation_plans=result.operation_plans)
        assert audit.observe(built.path,rows,matching)[0]['status']=='verified'
    with zipfile.ZipFile(built.path) as z:files={n:z.read(n) for n in z.namelist()}
    chapter=next(n for n in files if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
    tree=ET.fromstring(files[chapter]);quote=next(tree.iter(audit.N+'blockquote'))
    images=list(quote.iter(audit.N+'img'));paths=[i.get('src') for i in images]
    if mutation=='pixels':files['OEBPS/'+paths[0]]=files['OEBPS/'+paths[0]]+b'changed'
    elif mutation=='substitute':images[0].set('src',paths[1])
    elif mutation=='reorder':
        images[0].set('src',paths[1]);images[1].set('src',paths[0])
    elif mutation=='missing':del files['OEBPS/'+paths[0]]
    else:quote.tag=audit.N+'p'
    files[chapter]=ET.tostring(tree)
    with zipfile.ZipFile(built.path,'w') as z:
        for n,data in files.items():z.writestr(n,data)
    assert audit.observe(built.path,rows,matching)[0]['status']=='not_found'


@pytest.mark.parametrize('fault',['snapshot','source','selection','hash','fingerprint'])
def test_child_rejects_forged_audit_request(image_quote,tmp_path,monkeypatch,fault):
    import copy
    result,original,_=image_quote
    with NativeDocument(original.name,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc);call=doc.call
        def corrupt(op,args,**kw):
            if op=='operation_audit':
                args=copy.deepcopy(args)
                if fault=='snapshot':args['operations'][0]['snapshot_id']='0'*64
                elif fault=='source':args['operations'][0]['source_identity']='0'*64
                elif fault=='selection':args['operations'][0]['selected']=['op-'+'0'*24]
                elif fault=='hash':args['resources']={'images/fake.png':'0'*64}
                else:args['fingerprint']='0'*64
            return call(op,args,**kw)
        monkeypatch.setattr(doc,'call',corrupt)
        with pytest.raises((ValueError,ops.ContractError)):audit.capture(result,567,document=doc)


def test_changed_parent_source_geometry_is_not_a_current_audit(image_quote,tmp_path):
    result,original,_=image_quote
    with NativeDocument(original.name,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc)
        result.book.pages[0][1].bbox=(1,2,3,4)
        with pytest.raises(ops.ContractError):audit.capture(result,567,document=doc)


@pytest.mark.parametrize('fault',['shape','row','digest','path','projection'])
def test_parent_rejects_malformed_child_evidence(image_quote,tmp_path,monkeypatch,fault):
    import hashlib,json
    result,original,_=image_quote
    with NativeDocument(original.name,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc);call=doc.call
        def corrupt(op,args,**kw):
            answer=call(op,args,**kw)
            if op!='operation_audit':return answer
            rows,matching=answer;cid=rows[0]['candidate_id'];material=matching[cid]
            if fault=='shape':return {'rows':rows}
            if fault=='row':rows[0]['page_index0']=123
            if fault=='digest':rows[0]['source_atoms_sha256']='0'*64
            if fault=='path':material['resources']={'../../fake':'0'*64}
            if fault=='projection':
                material['projection']=[['text','invented']]
                rows[0]['source_atoms_sha256']=hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()
            return answer
        monkeypatch.setattr(doc,'call',corrupt)
        with pytest.raises(ValueError):audit.capture(result,567,document=doc)


def cached_fixture_conversion(book, raw, document, client, ledger, cache):
    """Synthetic exact-current typed receipts, not a reused paid source receipt.
    Keep the real pipeline's two-stage admission/cache/ledger path at cap0."""
    from cps.services.reflow import enriched_source, pipeline, structural_pipeline, typed_model, prompts
    from cps.services.reflow.operation_cache import OperationCache
    canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    prepared=ops.prepare(book,document,0,typed_model.SOURCE_REVISION,{'layer':'native'},
        source_page=canonical,raw_page=raw)
    selected=next(c['candidate_id'] for c in prepared.candidates() if c['kind']=='quote')
    proposal=dict(protocol=ops.PROTOCOL,snapshot_id=prepared.snapshot_id,select=[selected])
    plan=prepared.accept(book,document,proposal,source_page=canonical)
    verification=ops.prepare_verification(book,document,plan,source_page=canonical)
    # Public typed response contract is carried by the prompt, not invented here.
    request1=prompts.operation_request(prepared.model_view())
    request2=prompts.verification_request(verification.model_view())
    import json
    verify=json.loads(request2['messages'][1]['content'][1]['text'])['empty_response']
    verify['approve']=[selected]
    cache=OperationCache(cache.directory)
    for stage,request,response in [('proposer',request1,proposal),('verifier',request2,verify)]:
        wire=client.stages[stage].prepare_request(request,prepared.raster)
        token,saved=cache.claim(wire.sha256)
        if saved is None:cache.finish(wire.sha256,token,response=response)
    value=pipeline.ReflowResult(book=book,raw_pages=[raw],page_html={0:build_epub.page_fragment(book,0)},
        fingerprint=document.fingerprint)
    return structural_pipeline.run_structural(document,client=client,ledger=ledger,cache=cache,
        prepared_result=value)


@pytest.mark.parametrize('failure',[None,'death','cancel'])
def test_real_task_audits_image_without_parent_native_work_or_publication_on_failure(
        rig,image_quote,tmp_path,monkeypatch,failure):
    import os,signal,shutil,pymupdf
    from pathlib import Path
    from cps.services.reflow import structural_pipeline,ledger as ledger_mod
    from cps.services.worker import STAT_FINISH_SUCCESS,STAT_FAIL,STAT_ENDED
    result,original,_=image_quote;raw=extract.read_page(original,0)
    shutil.copyfile(original.name,rig.folder/'Book - Author.pdf')
    previous=rig.folder/'Book - Author.epub';previous.write_bytes(b'prior image-audit artifact')
    rig.formats['EPUB']=SimpleNamespace(name='Book - Author',format='EPUB')
    client=structural_pipeline.TwoStageClient('inert-never-sent',enabled=True)
    monkeypatch.setattr(rig.mod,'make_client',lambda *_:client)
    def convert(self,document,client,ledger,cache):
        return cached_fixture_conversion(result.book,raw,document,client,ledger,cache)
    monkeypatch.setattr(rig.mod.TaskReflowPdf,'_convert',convert)
    monkeypatch.setattr(pymupdf,'open',lambda *a,**k: (_ for _ in ()).throw(AssertionError('parent native open')))
    task=rig.mod.TaskReflowPdf(5,7,dict(mode='full',cost_cap_usd=0,replace_existing_epub=True))
    real=NativeDocument.call;seen=[]
    def call(self,op,args,**kw):
        if op=='operation_audit':
            seen.append(op)
            assert set(args)=={'book_id','fingerprint','operations','stage_records'}
            if failure=='death':os.kill(self.process.pid,signal.SIGKILL)
            if failure=='cancel':task.stat=STAT_ENDED
        return real(self,op,args,**kw)
    monkeypatch.setattr(NativeDocument,'call',call)
    task.run(None)
    assert seen
    assert task.stat=={None:STAT_FINISH_SUCCESS,'death':STAT_FAIL,'cancel':STAT_ENDED}[failure],task.error
    entries=ledger_mod.Ledger(str(Path(rig.root)/'jobs/5'/f'{task.job_id}.jsonl'),0).entries('operation_audit')
    row=_ledger_rows(rig)[0];assert row['spend_usd']==row['pending_usd']==0
    assert not list((Path(rig.root)/'native-scratch').iterdir())
    if failure:
        assert previous.read_bytes()==b'prior image-audit artifact' and not entries
        from cps import app,web
        with monkeypatch.context() as m:
            m.setattr(app,'_got_first_request',app._got_first_request)
            m.setattr(web,'config',SimpleNamespace(config_trustedhosts='',config_use_google_drive=False))
            with app.test_client() as webclient:assert webclient.get('/__audit_parent_alive__').status_code==404
    else:
        assert build_epub.validate(task.results['path'])==[]
        observed=next(e for e in entries if e['event']=='emitted')
        assert observed['operations'][0]['status']=='verified'
        assert all(e['schema']==audit.VERSION for e in entries)
