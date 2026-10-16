"""Untrusted cache path shape cannot be financial proof or abort sound Source."""
from tests.unit.test_round7_source_unit_financial_quarantine import setup,hold,admit,no_dispatch,unchanged_hold

def corrupt_pending_path(tmp_path):
 r,images,answer=setup(tmp_path);aid,path,bound=hold(r,images[0]);path.unlink();path.mkdir()
 return r,images,aid,path,bound

def test_pending_directory_is_not_admitted_or_released_as_financial_evidence(tmp_path,monkeypatch):
 r,images,aid,path,bound=corrupt_pending_path(tmp_path);calls=no_dispatch(monkeypatch)
 assert admit(r,images[0])is False,'Directory is an untrusted cache object, not a request receipt or fatal original-source identity'
 assert calls==[]and path.is_dir();unchanged_hold(r,aid,bound)

def test_sound_source_unit_degrades_without_raw_abort_when_pending_path_is_directory(tmp_path,monkeypatch):
 r,images,aid,path,bound=corrupt_pending_path(tmp_path);calls=no_dispatch(monkeypatch)
 result=r.read_unit(images[0],page=0)
 assert result['degraded_source']and result['answer']is None and result['image_sha256']
 assert any(e['transition']=='VISIBLE_SOURCE_DEGRADATION'for e in result['recovery_events'])
 assert calls==[]and path.is_dir();unchanged_hold(r,aid,bound)
