"""Publication recovery mutates only exact current source/library identities."""
import os
from pathlib import Path
import pytest
from cps.services.reflow import publication as p,ledger as l
from tests.fixtures.forking import run_forked

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
        assert run_forked(child,5)==0 and receiver.recv() is False
    with p.lock(folder,target,blocking=False) as acquired:assert acquired


def test_symlink_target_is_refused_before_backup_or_journal_mutation(tmp_path):
    root=tmp_path/'library';root.mkdir();outside=tmp_path/'outside';outside.write_bytes(b'user')
    target=root/'book.epub';target.symlink_to(outside)
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'candidate')
    source=root/'source.pdf';source.write_bytes(b'PDF');led=l.Ledger(tmp_path/'ledger',5,'job')
    with pytest.raises(p.PublicationConflict):p.prepare(led,str(root),1,'',str(source),str(target),str(stage),None,{'name':'book','size':9})
    assert not led.entries() and not (stage/'previous.epub').exists() and outside.read_bytes()==b'user'


def test_matching_bytes_in_another_book_folder_are_not_owned_by_this_journal(prepared):
    root,source,target,stage,led,record=prepared
    other=root/'another-book';other.mkdir()
    other_target=other/'book.epub';other_target.write_bytes((stage/'candidate.epub').read_bytes())
    other_stage=other/'.reflow-job-other';other_stage.mkdir()
    (other_stage/'previous.epub').write_bytes(target.read_bytes())
    forged=dict(record,target='another-book/book.epub',staging='another-book/.reflow-job-other')
    before=other_target.read_bytes()
    with pytest.raises(p.PublicationConflict):p.reconcile(led,str(root),forged,record['previous_format'],'',str(source))
    assert other_target.read_bytes()==before


@pytest.mark.parametrize('code',['EXDEV','EPERM','ENOTSUP','EOPNOTSUPP','EMLINK'])
def test_a_library_that_cannot_hard_link_still_gets_an_exact_backup_by_copy(tmp_path,monkeypatch,code):
    """SMB, FUSE and rclone mounts refuse hard links; every "replace existing
    EPUB" then failed at os.link (N4). The backup is an exact, synced copy
    instead -- verified against the identity the journal records -- and a crash
    after the replace still rolls back to the previous bytes. Breaks if the
    backup requires a hard link again."""
    import errno
    root=tmp_path/'library';root.mkdir();source=root/'source.pdf';source.write_bytes(b'PDF')
    target=root/'book.epub';target.write_bytes(b'previous user EPUB')
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'new document')
    led=l.Ledger(tmp_path/'jobs/1/job.jsonl',5,'job')
    def refused(*_args,**_kwargs):raise OSError(getattr(errno,code),'hard links are not supported here')
    monkeypatch.setattr(p.os,'link',refused)
    record=p.prepare(led,str(root),1,'',str(source),str(target),str(stage),{'name':'book','size':18},{'name':'book','size':12})
    backup=stage/'previous.epub'
    assert backup.read_bytes()==b'previous user EPUB' and not os.path.samefile(backup,target)
    assert record['previous']==p.identity(str(backup))
    assert set(os.listdir(stage))=={'candidate.epub','previous.epub'}
    # The process dies after the replace and before the database commit.
    os.replace(stage/'candidate.epub',target)
    assert p.reconcile(led,str(root),p.pending(led),record['previous_format'],'',str(source))=='rolled_back'
    assert target.read_bytes()==b'previous user EPUB' and not stage.exists()


def test_a_hard_link_failure_that_is_not_about_support_is_not_papered_over(tmp_path,monkeypatch):
    """Only 'this filesystem cannot link' falls back to a copy. A missing file or
    an I/O error is a real failure and stops the publication before any journal
    entry, as before."""
    import errno
    root=tmp_path/'library';root.mkdir();source=root/'source.pdf';source.write_bytes(b'PDF')
    target=root/'book.epub';target.write_bytes(b'old')
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'new')
    led=l.Ledger(tmp_path/'jobs/1/job.jsonl',5,'job')
    def broken(*_args,**_kwargs):raise OSError(errno.EIO,'input/output error')
    monkeypatch.setattr(p.os,'link',broken)
    with pytest.raises(OSError):
        p.prepare(led,str(root),1,'',str(source),str(target),str(stage),{'name':'book','size':3},{'name':'book','size':3})
    assert not led.entries('publication') and not (stage/'previous.epub').exists()


def test_a_symlinked_library_root_is_followed_once_and_containment_still_holds(tmp_path):
    """A library configured as /books -> /mnt/nas/books was refused outright: every
    path under it had a realpath different from its spelling (N4). The root's own
    link is now resolved, and the check that nothing BELOW the root is a symlink
    or escapes it is unchanged."""
    real=tmp_path/'nas-books';(real/'Author/Book (5)').mkdir(parents=True)
    link=tmp_path/'books';link.symlink_to(real,target_is_directory=True)
    inside=link/'Author/Book (5)/Book.epub'
    assert p.relative(str(link),str(inside))=='Author/Book (5)/Book.epub'
    assert p.resolve(str(link),'Author/Book (5)/Book.epub')==str(inside)
    # The real spelling of the same file is the same library path.
    assert p.relative(str(link),str(real/'Author/Book (5)/Book.epub'))=='Author/Book (5)/Book.epub'
    outside=tmp_path/'elsewhere';outside.mkdir()
    (real/'Author/Escape').symlink_to(outside,target_is_directory=True)
    for escaping in (link/'..'/'elsewhere'/'x.epub',link/'Author/Escape/x.epub',outside/'x.epub'):
        with pytest.raises(p.PublicationConflict):p.relative(str(link),str(escaping))


def test_a_backup_copy_that_is_not_the_measured_file_refuses_before_publishing(tmp_path,monkeypatch):
    """The copy fallback is only a backup if it IS the file the journal measured.
    A copy that comes out different -- the EPUB changed while it was read, or the
    mount returned a short read -- is a conflict before anything is journalled or
    replaced, never a wrong "previous" to roll back to later."""
    import errno
    root=tmp_path/'library';root.mkdir();source=root/'source.pdf';source.write_bytes(b'PDF')
    target=root/'book.epub';target.write_bytes(b'previous user EPUB')
    stage=root/'.reflow-job-owned';stage.mkdir();(stage/'candidate.epub').write_bytes(b'new document')
    led=l.Ledger(tmp_path/'jobs/1/job.jsonl',5,'job')
    def refused(*_args,**_kwargs):raise OSError(errno.EXDEV,'Invalid cross-device link')
    def short_read(_original,copy,*_args):copy.write(b'previous user')
    monkeypatch.setattr(p.os,'link',refused)
    monkeypatch.setattr(p.shutil,'copyfileobj',short_read)
    with pytest.raises(p.PublicationConflict):
        p.prepare(led,str(root),1,'',str(source),str(target),str(stage),{'name':'book','size':18},{'name':'book','size':12})
    assert not led.entries('publication')
    assert set(os.listdir(stage))=={'candidate.epub'}
    assert target.read_bytes()==b'previous user EPUB'
