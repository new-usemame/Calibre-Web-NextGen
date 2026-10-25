"""Untrusted unit cache never publishes foreign text; global destination/cancel stay fatal."""
import copy,hashlib,json,errno,requests
import pytest
from cps.services.reflow import vision_reading as vr,vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.round7_recovery_test_support import fixture,publish,h,TEXTS
from tests.unit.round7_public_note_contract_support import local_ocr_visible

@pytest.mark.parametrize('identity',['image_sha256','config_sha256'])
def test_wrong_unit_cache_identity_preserves_exact_rejected_bytes_and_healthy_cached_pages(tmp_path,monkeypatch,identity):
    doc,_,prepared=fixture(tmp_path,monkeypatch);reader=vr.Reader(None,tmp_path/'real-reading-cache',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02));saved={}
    config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    for n in range(3):
        image=SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90);digest=hashlib.sha256(image).hexdigest();answer=copy.deepcopy(prepared[n][2])
        value=dict(image_sha256=digest,config_sha256=config,answer=answer,cost_usd=.001)
        if n==1:value[identity]='foreign-untrusted-'+identity;answer['blocks'][0]['text']='Untrusted foreign cache prose must never be published.'
        path=reader.cache/(digest+'-'+config+'.json');path.write_text(json.dumps(value));saved[n]=(path,path.read_bytes())
    network=[]
    def forbidden(*args,**kwargs):network.append('unexpected network');raise AssertionError('offline cache rejection cannot dispatch')
    monkeypatch.setattr(requests,'post',forbidden)
    path,report,docs=publish(doc,reader,tmp_path)
    assert not network and report['reread_pages']==[]
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0
    assert all('Untrusted foreign cache' not in ''.join(root.itertext()) for root in docs.values())
    assert saved[0][0].read_bytes()==saved[0][1] and saved[2][0].read_bytes()==saved[2][1]
    assert any(p.read_bytes()==saved[1][1] for p in reader.cache.rglob('*') if p.is_file()),'rejected cache evidence remains exact'
    local_ocr_visible(path,report,{1:[TEXTS[1]]},non_local_pages={0,2})

@pytest.mark.parametrize('fatal',['cancel','destination_full'])
def test_global_cancellation_and_destination_storage_failure_do_not_silently_source_replace(tmp_path,monkeypatch,fatal):
    doc,reader,_=fixture(tmp_path,monkeypatch);reached=[]
    if fatal=='cancel':
        original=reader.read
        def stopped(image):reached.append('cancel');raise build_epub.BuildCancelled('synthetic explicit caller cancellation')
        reader.read=stopped;expected=build_epub.BuildCancelled
    else:
        def full(*args,**kwargs):reached.append('destination ENOSPC');raise OSError(errno.ENOSPC,'synthetic full destination')
        monkeypatch.setattr(build_epub._Package,'finish',full);expected=OSError
    out=tmp_path/'global-fatal.epub'
    try:
        with pytest.raises(expected):vp.convert(doc,str(out),reader)
    finally:doc.close()
    assert reached,'actual global boundary must be reached'
    assert not out.exists() or not __import__('zipfile').is_zipfile(out),'failed global destination cannot claim completed book'
