"""A physical page gets one targeted read across later assembly failures."""
import copy,hashlib,importlib.util,zipfile
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels,vision_reading as vr,build_epub
from cps.services.reflow.source_display import SourceDisplay
from cps.services.reflow.ledger import Ledger
_spec=importlib.util.spec_from_file_location('r7_reread_support',Path(__file__).with_name('round7_recovery_test_support.py'))
s=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(s)
BAD='The disputed azure specimen remains complete after review.'
LOCAL='The healthy same-page maple paragraph remains independently live.'


@pytest.mark.parametrize('reread_succeeds',[True,False])
def test_one_reread_across_alignment_and_real_duplicate_asset_failure(tmp_path,monkeypatch,reread_succeeds):
    prepared=[]
    for n,text in enumerate(s.TEXTS):
        page=s.h.InkPage();page.text(BAD if n==1 else text,'body',y=45)
        if n==1:
            page.figure('art',caption=False)
            page.text(LOCAL,'healthy',y=230)
        prepared.append(page.finish())
    doc=pymupdf.open()
    for image,_,_ in prepared:
        page=doc.new_page(width=s.h.W,height=s.h.H);page.insert_image(page.rect,stream=image)
    mapping={hashlib.sha256(SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90)).hexdigest():n for n in range(3)}
    ledger=Ledger(tmp_path/'ledger.jsonl',cap_usd=.02);reached=[]
    class Reader:
        cache=tmp_path/'readings'
        calls=[]
        rereads=0
        def read(self,image):
            n=mapping[hashlib.sha256(image).hexdigest()];self.calls.append(n)
            return {'answer':copy.deepcopy(prepared[n][2]),'cached':True,'new_cost_usd':0}
        def reread(self,image):
            self.rereads+=1;assert self.rereads==1 and mapping[hashlib.sha256(image).hexdigest()]==1
            reservation=ledger.reserve_attempt('synthetic-targeted',.001);ledger.reconcile_attempt(reservation,.001)
            if not reread_succeeds:raise ValueError('synthetic known-billed targeted reading remains unusable')
            return {'answer':copy.deepcopy(prepared[1][2]),'cached':False,'new_cost_usd':.001}
    reader=Reader();reader.ledger=ledger;reader.cache.mkdir(parents=True)
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
    actual_align=vr.align
    def alignment(text,*args,**kwargs):
        result=actual_align(text,*args,**kwargs)
        if text==BAD and (not reached or not reread_succeeds):
            reached.append('owned_alignment');raise ValueError('synthetic initial owned alignment fails')
        return result
    monkeypatch.setattr(vr,'align',alignment)
    actual_image=build_epub._Package.image
    def duplicate(package,src,data,**kwargs):
        result=actual_image(package,src,data,**kwargs)
        if 'vision_p0001_art' in src:
            reached.append('original_duplicate_image_abort')
            # Reach the real strict duplicate-asset raise after an asset has
            # already been written. The whole failed ZIP must be discarded.
            actual_image(package,src,data,**kwargs)
        return result
    monkeypatch.setattr(build_epub._Package,'image',duplicate)
    path,report,docs=s.publish(doc,reader,tmp_path)
    body=s.h.body_texts(docs);assert body.count(LOCAL)==1
    assert body.count(BAD)==int(reread_succeeds), 'successful reread restores live unit; rejected reread retains only original SOURCE for that unit'
    assert reader.calls==[0,1,2] and reader.rereads==1
    assert reached.count('original_duplicate_image_abort')==1
    assert report['reread_pages']==[1] and ledger.spent()==.001 and ledger.pending_usd()==0
    assert report['new_cost_usd']==.001, 'known billed rejected reread cost cannot disappear'
    expected={(1,'art')} | (set() if reread_succeeds else {(1,'body')})
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}==expected
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist();assert len(names)==len(set(names))
        assert not any('vision_p0001_art' in name for name in names), 'partly written failed asset cannot leak into replayed publication'
