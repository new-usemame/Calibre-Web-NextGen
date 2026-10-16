"""Re-read of a concurrently changed untrusted marker must close admission."""
from pathlib import Path
from tests.unit.test_round7_source_unit_financial_quarantine import setup,hold,admit,no_dispatch,unchanged_hold

def test_pending_becomes_malformed_between_observations_closes_admission(tmp_path,monkeypatch):
 r,images,_=setup(tmp_path);aid,pending,bound=hold(r,images[0]);original=pending.read_bytes();calls=no_dispatch(monkeypatch);read_bytes=Path.read_bytes;observations=[]
 def changed(path):
  if path==pending:observations.append(str(path));return b'{' # actual second cache-byte observation is no longer valid JSON
  return read_bytes(path)
 monkeypatch.setattr(Path,'read_bytes',changed)
 assert admit(r,images[0])is False,'Untrusted marker changed after initial parse; close admission instead of raw JSON abort'
 assert observations and calls==[];unchanged_hold(r,aid,bound)
 monkeypatch.setattr(Path,'read_bytes',read_bytes);assert pending.read_bytes()==original
