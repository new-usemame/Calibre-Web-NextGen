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
        rows,matching=audit.capture(result,73,document=doc)
        built=build_epub.build(result.book,str(tmp_path/'actual.epub'),doc=doc,
            source_pages={0:result.operation_plans[0].prepared.source_page},operation_plans=result.operation_plans)
        assert audit.observe(built.path,rows,matching)[0]['status']=='verified'
        assert 'source_atoms_sha256' in rows[0]
        assert all(len(v)==64 for v in matching[rows[0]['candidate_id']]['resources'].values())
        # Build reissues equal factory values. They must not survive as stale
        # weak-key owners that revoke the next freshly reissued audit authority.
        again, again_matching = audit.capture(result, 73, document=doc)
        assert again == rows and again_matching == matching
        with pytest.raises(ops.ContractError):
            build_epub.build(result.book, str(tmp_path/'rejected.epub'), doc=doc,
                page_html={0:'<p>not the admitted source</p>'},
                source_pages={0:result.operation_plans[0].prepared.source_page},
                operation_plans=result.operation_plans)
        after_error, error_matching = audit.capture(result, 73, document=doc)
        assert after_error == rows and error_matching == matching


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
        rows,matching=audit.capture(result,73,document=doc)
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
        with pytest.raises((ValueError,ops.ContractError)):audit.capture(result,73,document=doc)


def test_changed_parent_source_geometry_is_not_a_current_audit(image_quote,tmp_path):
    result,original,_=image_quote
    with NativeDocument(original.name,scratch_root=tmp_path/'scratch') as doc:
        result=remote_result(result,original,doc)
        result.book.pages[0][1].bbox=(1,2,3,4)
        with pytest.raises(ops.ContractError):audit.capture(result,73,document=doc)


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
        with pytest.raises(ValueError):audit.capture(result,73,document=doc)



@pytest.mark.parametrize('failure', [None, 'death', 'cancel'])
def test_real_task_audits_source_image_without_parent_native_work_or_publication_on_failure(
        rig, image_quote, monkeypatch, failure):
    import hashlib
    import os
    import signal
    import pymupdf
    from pathlib import Path
    from xml.etree import ElementTree as ET
    from cps.services.reflow import ledger as ledger_mod
    from cps.services.worker import STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED
    _, original, _ = image_quote
    pixels = original[0].get_pixmap().tobytes('png')
    source_path = rig.folder / 'Book - Author.pdf'
    with pymupdf.open() as scanned:
        page = scanned.new_page(width=original[0].rect.width, height=original[0].rect.height)
        page.insert_image(page.rect, stream=pixels)
        scanned.save(source_path)
    fingerprint = extract.document_fingerprint(source_path)
    previous = rig.folder / 'Book - Author.epub'
    previous.write_bytes(b'prior image-audit artifact')
    rig.formats['EPUB'] = SimpleNamespace(name='Book - Author', format='EPUB')
    monkeypatch.setattr(pymupdf, 'open', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('parent native open')))
    task = rig.mod.TaskReflowPdf(5, 7, dict(mode='full', cost_cap_usd=0,
                                         replace_existing_epub=True))
    real = NativeDocument.call
    seen = []
    def call(self, op, args, **kw):
        if op == 'vision_convert':
            seen.append(op)
            assert args['page_numbers'] == [0]
            assert args['reader']['enabled'] is False
            if failure == 'death': os.kill(self.process.pid, signal.SIGKILL)
            if failure == 'cancel': task.stat = STAT_ENDED
        return real(self, op, args, **kw)
    monkeypatch.setattr(NativeDocument, 'call', call)
    task.run(None)
    assert seen
    assert task.stat == {None: STAT_FINISH_SUCCESS, 'death': STAT_FAIL,
                         'cancel': STAT_ENDED}[failure], task.error
    entries = ledger_mod.Ledger(Path(rig.root) / 'jobs/5' / f'{task.job_id}.jsonl', 0).entries('vision_artifact')
    row = _ledger_rows(rig)[0]
    assert row['spend_usd'] == row['pending_usd'] == 0
    assert not list((Path(rig.root) / 'native-scratch').iterdir())
    if failure:
        assert previous.read_bytes() == b'prior image-audit artifact' and not entries
        assert rig.local_db.session.commits == 0
        from cps import app, web
        with monkeypatch.context() as m:
            m.setattr(app, '_got_first_request', app._got_first_request)
            m.setattr(web, 'config', SimpleNamespace(config_trustedhosts='', config_use_google_drive=False))
            with app.test_client() as webclient:
                assert webclient.get('/__audit_parent_alive__').status_code == 404
    else:
        path = Path(task.results['path'])
        assert build_epub.validate(str(path)) == []
        assert len(entries) == 1 and entries[0]['event'] == 'validated'
        assert entries[0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entries[0]['bytes'] == path.stat().st_size
        with zipfile.ZipFile(path) as archive:
            import json
            sidecar = json.loads(archive.read('META-INF/reflow.json'))
            assert sidecar['source_document_sha256'] == fingerprint
            assert sidecar['pages']['0']['source'] == 'original_source'
            units = sidecar['degraded_units']
            assert len(units) == 1 and units[0]['page'] == 0
            unit = units[0]
            image = archive.read('OEBPS/' + unit['source_image'])
            assert image.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n'))
            documents = [ET.fromstring(archive.read(name)) for name in archive.namelist()
                         if name.endswith('.xhtml')]
            matches = [element for tree in documents for element in tree.iter()
                       if element.get('id') == unit['source_reference']]
            assert len(matches) == 1
            assert any(element.tag.endswith('}img') and element.get('src') == unit['source_image']
                       for element in matches[0].iter())
            assert any(element.tag.endswith(('}p', '}figcaption')) and ''.join(element.itertext()).strip()
                       for element in matches[0].iter())
