"""Current per-job consent never bypasses the shared administrator budget."""
import inspect
from unittest.mock import patch
import pytest
from tests.unit.test_reflow_api import mod,pdf_on_disk,_wire,_paid,_start,_json,_status,_ctx,_user
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('budget,code',[(0,'instance_budget_unavailable'),(.001,'instance_budget_exhausted')])
def test_legal_owned_quote_still_needs_shared_capacity(mod,monkeypatch,pdf_on_disk,budget,code):
    _wire(mod,monkeypatch,pdf_on_disk);_paid(mod,monkeypatch)
    monkeypatch.setattr(mod.config,'config_reflow_instance_budget_usd',budget,raising=False)
    response,added=_start(mod,dict(consent=True,review_mode='source_verified',cost_cap_usd=1))
    assert _status(response)==409 and _json(response)['error']['code']==code
    added.assert_not_called()
    response,added=_start(mod,dict(consent=True,review_mode='deterministic'))
    assert _status(response)==202 and added.called


@pytest.mark.parametrize('damage',['corrupt','missing','rollback'])
def test_corrupt_shared_state_does_not_break_free_estimate_or_settings(mod,monkeypatch,pdf_on_disk,damage):
    from pathlib import Path
    _wire(mod,monkeypatch,pdf_on_disk);_paid(mod,monkeypatch)
    monkeypatch.setattr(mod.config,'config_reflow_instance_budget_usd',1,raising=False)
    import shutil
    shared=mod.tasks_reflow.instance_budget_store()
    assert shared.status()['status']=='available'
    p=shared.path;shutil.copyfile(p,p.with_suffix('.old'))
    shared.reserve('global-only',.8,job='a',book='a',user='a',context={})
    if damage=='corrupt':p.write_text('broken financial evidence')
    elif damage=='missing':p.unlink()
    else:p.with_suffix('.old').replace(p)
    with _ctx('/api/v1/books/5/reflow/estimate'),patch.object(mod,'current_user',_user()):
        response=inspect.unwrap(mod.reflow_estimate)(5)
    assert _json(response)['instance_budget']==dict(status='unavailable',remaining_usd=None,window_hours=24)
    response,added=_start(mod,dict(consent=True,review_mode='deterministic'))
    assert _status(response)==202 and added.called
    response,added=_start(mod,dict(consent=True,review_mode='source_verified',cost_cap_usd=1))
    assert _status(response)==409 and not added.called


def test_dispatch_reads_committed_admin_limit_instead_of_stale_process_cache(mod,monkeypatch,tmp_path):
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from types import SimpleNamespace
    from cps import config_sql
    from cps.services.reflow.shared_budget import BudgetError
    engine=create_engine('sqlite:///'+str(tmp_path/'settings.db'))
    config_sql._Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO settings (id,config_reflow_instance_budget_usd) VALUES (1,0.8)'))
    session=sessionmaker(bind=engine)()
    monkeypatch.setattr(mod.tasks_reflow,'config',SimpleNamespace(_session=session,config_reflow_instance_budget_usd=999))
    shared=mod.tasks_reflow.instance_budget_store()
    shared.reserve('prior',.6,job='j',book='b',user='u',context={})
    with engine.begin() as connection:
        connection.execute(text('UPDATE settings SET config_reflow_instance_budget_usd=0.1 WHERE id=1'))
    assert mod.tasks_reflow.instance_budget_usd()==.1
    with pytest.raises(BudgetError):shared.reserve('next',.01,job='j2',book='b2',user='u2',context={})
    session.close();engine.dispose()
