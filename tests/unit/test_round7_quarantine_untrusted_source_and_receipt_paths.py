"""Sound Source and financial holds survive untrusted local cache path objects."""
import errno,hashlib
from pathlib import Path
import pytest
from tests.unit.test_round7_source_unit_financial_quarantine import setup,hold,admit,no_dispatch,unchanged_hold

def source_path(r,image):return r.cache/(hashlib.sha256(image).hexdigest()+'-'+r._configuration()+'.liability-source.jpg')

def test_source_witness_directory_is_not_financial_admission(tmp_path,monkeypatch):
 r,images,_=setup(tmp_path);aid,pending,bound=hold(r,images[0]);original=pending.read_bytes();source=source_path(r,images[0]);source.mkdir();calls=no_dispatch(monkeypatch)
 assert admit(r,images[0])is False
 assert source.is_dir()and pending.read_bytes()==original and calls==[];unchanged_hold(r,aid,bound)

def test_changed_source_witness_directory_closes_fresh_financial_admission(tmp_path,monkeypatch):
 r,images,_=setup(tmp_path);aid,pending,bound=hold(r,images[0]);assert admit(r,images[0])is True
 source=source_path(r,images[0]);source.unlink();source.mkdir();calls=no_dispatch(monkeypatch)
 assert r.financial_dispatch_allowed()is False
 assert source.is_dir()and calls==[];unchanged_hold(r,aid,bound)

def test_preserved_receipt_directory_cannot_claim_retained_bytes(tmp_path,monkeypatch):
 r,images,_=setup(tmp_path);aid,pending,bound=hold(r,images[0]);calls=no_dispatch(monkeypatch)
 digest=hashlib.sha256(pending.read_bytes()).hexdigest();target=r.cache/'rejected'/(digest+'.pending');target.mkdir(parents=True)
 # Keep admission closed for a real unproved journal/marker mismatch.
 import json
 record=json.loads(pending.read_text());record['bound_usd']=bound/2;pending.write_text(json.dumps(record))
 digest=hashlib.sha256(pending.read_bytes()).hexdigest();target=r.cache/'rejected'/(digest+'.pending');target.mkdir()
 outcome=r.read_unit(images[0],page=0)
 assert outcome['degraded_source']and outcome['answer']is None and calls==[]
 assert outcome['rejected_receipts'],'Unreadable retained-receipt objects need explicit metadata'
 for receipt in outcome['rejected_receipts']:
  assert (receipt.get('sha256')and Path(receipt['path']).is_file())or receipt.get('error_type'),'Directory must not masquerade as exact preserved cache bytes'
 assert target.is_dir();unchanged_hold(r,aid,bound)

@pytest.mark.parametrize('code',[errno.ENOSPC,errno.EACCES,errno.EROFS,errno.EMFILE,errno.ENFILE])
def test_fatal_cache_storage_error_remains_global_reason(tmp_path,monkeypatch,code):
 r,images,_=setup(tmp_path);aid,pending,bound=hold(r,images[0]);calls=no_dispatch(monkeypatch);original=Path.read_text
 def fail_owned(path,*a,**kw):
  if path==pending:raise OSError(code,'synthetic fatal cache storage')
  return original(path,*a,**kw)
 monkeypatch.setattr(Path,'read_text',fail_owned)
 with pytest.raises(OSError)as caught:admit(r,images[0])
 assert caught.value.errno==code and calls==[];unchanged_hold(r,aid,bound)
