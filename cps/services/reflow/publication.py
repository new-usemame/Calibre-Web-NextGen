# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover the filesystem/format-commit boundary without overwriting later work.

Journal paths are relative to the current library. Every recovery mutation requires
both the expected database state and exact file identities. A conflict preserves
all evidence for the operator; it never guesses that a later file is ours.
"""
import fcntl
import hashlib
import os
import shutil
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
    root=os.path.realpath(root)
    absolute=os.path.abspath(path)
    if os.path.realpath(absolute)!=absolute or os.path.commonpath([root,absolute])!=root:
        raise PublicationConflict('publication path escapes the current library or uses a symlink')
    return os.path.relpath(absolute,root)


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


def pending(ledger):
    events=ledger.entries('publication')
    if not events:return None
    last=events[-1]
    if last.get('event') in ('rolled_back','retained'):return None
    prepared=next((e for e in reversed(events) if e.get('event')=='prepared'),None)
    return prepared


def prepare(ledger,root,book_id,book_path,source,target,staging,previous_format,desired_format,expected_source=None):
    candidate=os.path.join(staging,'candidate.epub')
    backup=os.path.join(staging,'previous.epub')
    for path in (source,target,staging,candidate,backup):relative(root,path)
    old=identity(target);new=identity(candidate)
    source_identity=identity(source)
    if source_identity is None:raise PublicationConflict('publication source PDF is missing')
    if expected_source and source_identity['sha256']!=expected_source:
        raise PublicationConflict('publication source changed during conversion')
    if new is None:raise PublicationConflict('publication candidate is missing')
    if old is not None:os.link(target,backup)
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
