"""Publication recovery mutates only exact current source/library identities."""
import os
from pathlib import Path
import pytest
from cps.services.reflow import publication as p,ledger as l

pytestmark=pytest.mark.unit

@pytest.fixture
def prepared(tmp_path):
    root=tmp_path/'library';root.mkdir();source=root/'source.pdf';source.write_bytes(b'PDF')
    target=root/'book.epub';target.write_bytes(b'old')
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'new document')
    led=l.Ledger(tmp_path/'jobs/1/job.jsonl',5,'job')
    record=p.prepare(led,str(root),1,'',str(source),str(target),str(stage),{'name':'book','size':3},{'name':'book','size':12})
    return root,source,target,stage,led,record

@pytest.mark.parametrize('field,value',[('target','../outside'),('target','/absolute'),('staging','.reflow-otherjob-owned'),('source','../outside')])
def test_forged_recovery_paths_preserve_all_files(prepared,field,value):
    root,source,target,stage,led,record=prepared
    os.replace(stage/'candidate.epub',target)
    record=dict(record,**{field:value})
    before={str(f):f.read_bytes() for f in root.rglob('*') if f.is_file()}
    with pytest.raises(p.PublicationConflict):p.reconcile(led,str(root),record,record['previous_format'],'',str(source))
    assert before=={str(f):f.read_bytes() for f in root.rglob('*') if f.is_file()}


def test_recovery_after_cleanup_before_receipt_is_idempotent(prepared,monkeypatch):
    root,source,target,stage,led,record=prepared
    os.replace(stage/'candidate.epub',target)
    real_record=led.record
    def interrupted(event,**kw):
        if event.get('event')=='rolled_back':raise OSError('receipt unavailable')
        return real_record(event,**kw)
    monkeypatch.setattr(led,'record',interrupted)
    with pytest.raises(OSError):p.reconcile(led,str(root),record,record['previous_format'],'',str(source))
    assert target.read_bytes()==b'old' and not stage.exists()
    monkeypatch.setattr(led,'record',real_record)
    assert p.reconcile(led,str(root),p.pending(led),record['previous_format'],'',str(source))=='rolled_back'
    assert p.pending(led) is None


def test_identical_prior_and_desired_metadata_without_commit_receipt_rolls_back_conservatively(prepared):
    root,source,target,stage,led,record=prepared
    # Same name and byte length do not prove that the database commit ran.
    record['desired_format']=dict(record['previous_format'])
    os.replace(stage/'candidate.epub',target)
    assert p.reconcile(led,str(root),record,record['previous_format'],'',str(source))=='rolled_back'
    assert target.read_bytes()==b'old'


def test_a_separate_process_cannot_recover_while_publication_lock_is_owned(tmp_path):
    import multiprocessing
    folder=str(tmp_path/'locks');target=str(tmp_path/'book.epub')
    receiver,sender=multiprocessing.Pipe(False)
    def child():
        with p.lock(folder,target,blocking=False) as acquired:sender.send(acquired)
    with p.lock(folder,target):
        process=multiprocessing.get_context('fork').Process(target=child)
        process.start();process.join(5)
        assert process.exitcode==0 and receiver.recv() is False
    with p.lock(folder,target,blocking=False) as acquired:assert acquired


def test_symlink_target_is_refused_before_backup_or_journal_mutation(tmp_path):
    root=tmp_path/'library';root.mkdir();outside=tmp_path/'outside';outside.write_bytes(b'user')
    target=root/'book.epub';target.symlink_to(outside)
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'candidate')
    source=root/'source.pdf';source.write_bytes(b'PDF');led=l.Ledger(tmp_path/'ledger',5,'job')
    with pytest.raises(p.PublicationConflict):p.prepare(led,str(root),1,'',str(source),str(target),str(stage),None,{'name':'book','size':9})
    assert not led.entries() and not (stage/'previous.epub').exists() and outside.read_bytes()==b'user'
