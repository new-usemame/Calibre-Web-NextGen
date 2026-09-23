# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover the filesystem/format-commit boundary without overwriting later work.

Journal paths are relative to the current library. Every recovery mutation requires
both the expected database state and exact file identities. A conflict preserves
all evidence for the operator; it never guesses that a later file is ours.
"""
import errno
import fcntl
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager


class PublicationConflict(ValueError):
    pass


def identity(path):
    if not os.path.exists(path):
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        raise PublicationConflict('publication path is not a regular owned file')
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return dict(sha256=digest.hexdigest(),bytes=os.path.getsize(path))


def sync(path):
    flags=os.O_RDONLY
    fd=os.open(path,flags)
    try:os.fsync(fd)
    finally:os.close(fd)


def relative(root,path):
    """``path`` as a library-relative path, or a conflict if it is not in the library.

    The library root is followed once: a root configured as a symlink (``/books ->
    /mnt/nas/books``, common on NAS installs) is resolved, and a path spelled
    through it is re-anchored on the real root (N4 of the 7daffa5 retest -- every
    full publication was refused there). Below the root nothing is followed: a
    path whose own components are symlinks, or that leaves the real root, is
    refused exactly as before.
    """
    anchor=os.path.abspath(root)
    real_root=os.path.realpath(root)
    absolute=os.path.abspath(path)
    if absolute==anchor or absolute.startswith(anchor.rstrip(os.sep)+os.sep):
        absolute=os.path.normpath(os.path.join(real_root,os.path.relpath(absolute,anchor)))
    if os.path.realpath(absolute)!=absolute or os.path.commonpath([real_root,absolute])!=real_root:
        raise PublicationConflict('publication path escapes the current library or uses a symlink')
    return os.path.relpath(absolute,real_root)


def resolve(root,path):
    if not isinstance(path,str) or os.path.isabs(path):
        raise PublicationConflict('publication journal path is not relative')
    joined=os.path.abspath(os.path.join(root,path))
    if relative(root,joined)!=path:
        raise PublicationConflict('publication journal path is not canonical')
    return joined


@contextmanager
def lock(directory,target,blocking=True):
    os.makedirs(directory,exist_ok=True)
    key=hashlib.sha256(os.path.realpath(target).encode()).hexdigest()
    with open(os.path.join(directory,key+'.lock'),'a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:yield True
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


#: Journal events after which there is nothing left for recovery to do.
TERMINAL=('rolled_back','retained','conflict_closed')


def pending(ledger):
    events=ledger.entries('publication')
    if not events:return None
    last=events[-1]
    if last.get('event') in TERMINAL:return None
    prepared=next((e for e in reversed(events) if e.get('event')=='prepared'),None)
    return prepared


def _owned_target(root,target,book_path,desired_format):
    folder=os.path.abspath(os.path.join(root,book_path))
    relative(root,folder)
    name=desired_format.get('name') if isinstance(desired_format,dict) else None
    if not isinstance(name,str) or os.path.dirname(target)!=folder or os.path.basename(target)!=name+'.epub':
        raise PublicationConflict('publication target does not belong to the current book format')


#: os.link failures that mean "this filesystem does not do hard links" (SMB,
#: FUSE, rclone, vfat, some overlay mounts) rather than "something is wrong".
_NO_HARD_LINKS=frozenset(code for code in (errno.EXDEV,errno.EPERM,errno.EMLINK,errno.ENOSYS,
    getattr(errno,'ENOTSUP',None),getattr(errno,'EOPNOTSUPP',None)) if code is not None)


def _keep_previous(target,backup,expected):
    """Preserve the EPUB about to be replaced, as ``backup``, exactly.

    A hard link costs nothing and cannot differ from the original. Where the
    filesystem refuses one (N4), an exact copy does the same job: it is written
    beside the backup name, synced, renamed into place, and then compared with
    the identity the journal is about to record -- a copy that is not the file we
    measured is a conflict before anything is published, never a bad backup.
    Any other link failure is a real failure and propagates as before.
    """
    try:
        os.link(target,backup);return
    except OSError as exc:
        if exc.errno not in _NO_HARD_LINKS:raise
    partial=backup+'.partial'
    try:
        with open(target,'rb') as original,open(partial,'xb') as copy:
            shutil.copyfileobj(original,copy,1024*1024)
            copy.flush();os.fsync(copy.fileno())
        os.replace(partial,backup)
    finally:
        if os.path.exists(partial):os.remove(partial)
    if identity(backup)!=expected:
        os.remove(backup)
        raise PublicationConflict('the previous EPUB changed while it was being backed up')


def prepare(ledger,root,book_id,book_path,source,target,staging,previous_format,desired_format,expected_source=None):
    candidate=os.path.join(staging,'candidate.epub')
    backup=os.path.join(staging,'previous.epub')
    for path in (source,target,staging,candidate,backup):relative(root,path)
    _owned_target(root,target,book_path,desired_format)
    old=identity(target);new=identity(candidate)
    source_identity=identity(source)
    if source_identity is None:raise PublicationConflict('publication source PDF is missing')
    if expected_source and source_identity['sha256']!=expected_source:
        raise PublicationConflict('publication source changed during conversion')
    if new is None:raise PublicationConflict('publication candidate is missing')
    if old is not None:_keep_previous(target,backup,old)
    sync(candidate)
    if old is not None:sync(backup)
    sync(staging);sync(os.path.dirname(staging))
    record=dict(kind='publication',event='prepared',version=1,book_id=book_id,book_path=book_path,
                source=relative(root,source),source_identity=source_identity,target=relative(root,target),
                staging=relative(root,staging),previous=old,candidate=new,
                previous_format=previous_format,desired_format=desired_format)
    ledger.record(record,durable=True)
    return record


def committed(ledger):
    ledger.record(dict(kind='publication',event='committed'),durable=True)


def reconcile(ledger,root,record,current_format,book_path,source):
    """Return retained/rolled_back, or raise while preserving every conflicting file."""
    if record.get('version')!=1 or record.get('book_path')!=book_path:
        raise PublicationConflict('publication book identity changed')
    target=resolve(root,record['target']);staging=resolve(root,record['staging'])
    _owned_target(root,target,book_path,record['desired_format'])
    expected_prefix='.reflow-'+str(ledger.job_id)+'-'
    if os.path.dirname(staging)!=os.path.dirname(target) or not os.path.basename(staging).startswith(expected_prefix):
        raise PublicationConflict('publication staging is not owned by this job')
    if relative(root,source)!=record['source'] or identity(source)!=record['source_identity']:
        raise PublicationConflict('publication original PDF changed')
    if current_format not in (record['previous_format'],record['desired_format']):
        raise PublicationConflict('publication format metadata changed after preparation')
    actual=identity(target);old=record['previous'];new=record['candidate']
    backup=os.path.join(staging,'previous.epub');candidate=os.path.join(staging,'candidate.epub')
    if os.path.isdir(staging) and set(os.listdir(staging))-{'candidate.epub','previous.epub'}:
        raise PublicationConflict('publication staging contains unexpected files')
    backup_state=identity(backup);candidate_state=identity(candidate)
    if backup_state not in (None,old) or candidate_state not in (None,new):
        raise PublicationConflict('publication staging file identity changed')
    events=ledger.entries('publication')
    start=max(i for i,e in enumerate(events) if e.get('event')=='prepared')
    committed_event=any(e.get('event')=='committed' for e in events[start:])
    if actual==new and current_format==record['desired_format'] and (committed_event or current_format!=record['previous_format']):
        outcome='retained'
        if not any(e.get('sha256')==new['sha256'] for e in ledger.entries('artifact')):
            ledger.record(dict(kind='artifact',**new),durable=True)
    elif actual==old and current_format==record['previous_format']:
        outcome='rolled_back'  # died before replace, or after rollback before receipt
    elif actual==new and current_format==record['previous_format'] and not committed_event:
        if old is not None:
            if backup_state!=old:raise PublicationConflict('publication backup is missing')
            os.replace(backup,target)
        else:os.remove(target)
        sync(os.path.dirname(target));outcome='rolled_back'
    else:
        raise PublicationConflict('publication file and metadata no longer match a recoverable state')
    # Record the decision before removing the last backup. A repeated recovery can
    # safely finish cleanup; no provider reservation or page accounting is touched.
    ledger.record(dict(kind='publication',event='recovery_decided',outcome=outcome),durable=True)
    if os.path.isdir(staging):shutil.rmtree(staging)
    sync(os.path.dirname(target))
    ledger.record(dict(kind='publication',event=outcome),durable=True)
    return outcome


def _copy_exact(source,destination):
    """Copy one file so that ``destination`` is ``source`` byte for byte, or raise."""
    want=identity(source)
    if identity(destination)==want:return want    # a repeated pass after a crash
    partial=destination+'.partial'
    try:
        with open(source,'rb') as original,open(partial,'wb') as copy:
            shutil.copyfileobj(original,copy,1024*1024)
            copy.flush();os.fsync(copy.fileno())
        os.replace(partial,destination)
    finally:
        if os.path.exists(partial):os.remove(partial)
    if identity(destination)!=want:
        raise PublicationConflict('preserved publication evidence does not match its original')
    return want


def owned_staging(root,relative_path,job_id):
    """The job's own staging folder under ``root``, or None if it is not there.

    Only a real directory, directly named ``.reflow-<job_id>-...``, inside the
    root and reached without symlinks, is ever returned -- anything else a
    journal names is not ours to move or remove.
    """
    try:
        path=resolve(root,relative_path)
    except PublicationConflict:
        return None
    if not os.path.basename(path).startswith('.reflow-%s-'%job_id):
        return None
    if os.path.islink(path) or not os.path.isdir(path):
        return None
    return path


def close_conflict(ledger,root,record,destination,reason,current_book_path=None):
    """Take a conflicted publication's evidence out of the library, exactly.

    A conflict means recovery could not prove which state is right, so it kept
    every file -- inside the book's folder, where a hidden ``.reflow-*`` directory
    holding two EPUBs then stayed forever (N4). When the conflict still stands on
    a later recovery pass, the staging files are copied to ``destination`` (the
    Reflow data folder), each copy verified against the original, with a manifest
    naming the reason and the journal record; the journal is closed durably; and
    only then is the staging folder removed from the library. Nothing is deleted
    that has not first been preserved: ``previous.epub`` may be the only copy of
    the EPUB the user had. Returns the evidence folder, or None when the staging
    folder no longer exists.
    """
    staging=owned_staging(root,record.get('staging'),ledger.job_id)
    if staging is None and current_book_path:
        # The book was moved (retitled) after the crash; its folder moved too.
        moved=os.path.join(current_book_path,os.path.basename(str(record.get('staging') or '')))
        staging=owned_staging(root,moved,ledger.job_id)
    evidence=None
    if staging is not None:
        os.makedirs(destination,exist_ok=True)
        files={}
        for name in sorted(os.listdir(staging)):
            path=os.path.join(staging,name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise PublicationConflict('publication staging contains unexpected entries')
            files[name]=_copy_exact(path,os.path.join(destination,name))
        manifest=dict(job_id=ledger.job_id,reason=reason,closed=time.time(),
                      staging=relative(root,staging),record=record,files=files)
        partial=os.path.join(destination,'manifest.json.partial')
        with open(partial,'w',encoding='utf-8') as handle:
            json.dump(manifest,handle,ensure_ascii=False,indent=1,sort_keys=True)
            handle.flush();os.fsync(handle.fileno())
        os.replace(partial,os.path.join(destination,'manifest.json'))
        sync(destination)
        evidence=destination
    ledger.record(dict(kind='publication',event='conflict_closed',reason=reason,
                       evidence=evidence),durable=True)
    if staging is not None:
        shutil.rmtree(staging)
        sync(os.path.dirname(staging))
    return evidence
