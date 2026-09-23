"""Current consent distinguishes source-only conversion from billed two-stage review."""
import inspect,os
from unittest.mock import patch
import pytest
from tests.unit.test_reflow_api import mod,pdf_on_disk,_wire,_start,_ctx,_user,_json,_status

pytestmark=pytest.mark.unit


def consent(mod,pdf_on_disk,**values):
    source=os.path.join(pdf_on_disk['library'],'Author/Book (5)',pdf_on_disk['name']+'.pdf')
    return dict(consent=True,consent_contract='source-review-1',source_sha256=mod.extract.document_fingerprint(source),
                review_mode='deterministic',mode='sample',**values)


def test_current_deterministic_consent_starts_without_a_provider_key(mod,monkeypatch,pdf_on_disk):
    _wire(mod,monkeypatch,pdf_on_disk,key='')
    response,added=_start(mod,consent(mod,pdf_on_disk))
    assert _status(response)==202
    task=added.call_args[0][1]
    assert task.options.review_mode=='deterministic' and task.options.cost_cap_usd==0


def test_an_administrator_without_the_edit_role_may_convert_like_every_other_edit_surface(mod,monkeypatch,pdf_on_disk):
    """Reflow files a new format onto a book, which the rest of the app lets an
    administrator do whether or not the account also carries the edit bit
    (editbooks.edit_required: ``role_edit() or role_admin()``). Refusing only
    here made an admin able to replace a book's EPUB by hand but not by
    conversion. Breaks if _require_edit goes back to checking role_edit alone."""
    _wire(mod,monkeypatch,pdf_on_disk,key='')
    admin_only=_user(edit=False,admin=True)
    with _ctx('/api/v1/books/5/reflow/estimate'),patch.object(mod,'current_user',admin_only):
        assert _status(inspect.unwrap(mod.reflow_estimate)(5))==200
    response,added=_start(mod,consent(mod,pdf_on_disk),user=admin_only)
    assert _status(response)==202,_json(response)
    added.assert_called_once()
    # Neither role is still no conversion.
    response,added=_start(mod,consent(mod,pdf_on_disk),user=_user(edit=False,admin=False))
    assert _status(response)==403
    added.assert_not_called()


def test_legacy_consent_cannot_authorize_the_new_billed_route(mod,monkeypatch,pdf_on_disk):
    _wire(mod,monkeypatch,pdf_on_disk)
    response,added=_start(mod,{'consent':True,'model_tier':'quality','cost_cap_usd':5},current=False)
    assert _status(response)==409 and _json(response)['error']['code']=='current_consent_required'
    added.assert_not_called()


def test_current_source_identity_is_required_even_for_free_conversion(mod,monkeypatch,pdf_on_disk):
    _wire(mod,monkeypatch,pdf_on_disk,key='')
    body=consent(mod,pdf_on_disk);body['source_sha256']='0'*64
    response,added=_start(mod,body)
    assert _status(response)==409 and _json(response)['error']['code']=='source_changed'
    added.assert_not_called()


def test_optional_review_cannot_bypass_the_quality_gate(mod,monkeypatch,pdf_on_disk):
    _wire(mod,monkeypatch,pdf_on_disk)
    body=consent(mod,pdf_on_disk);body.update(review_mode='source_verified',cost_cap_usd=1)
    response,added=_start(mod,body)
    assert _status(response)==409 and _json(response)['error']['code']=='review_unavailable'
    added.assert_not_called()


def test_invalid_or_missing_prepared_identity_cannot_start_paid_review(mod,monkeypatch,pdf_on_disk):
    _wire(mod,monkeypatch,pdf_on_disk)
    monkeypatch.setattr(mod.typed_model,'QUALITY_RELEASED',True)
    for identity in (None,{},[],123,'not-an-identity'):
        body=consent(mod,pdf_on_disk);body.update(review_mode='source_verified',cost_cap_usd=1,preparation_id=identity)
        response,added=_start(mod,body)
        assert _status(response)==409 and _json(response)['error']['code']=='estimate_stale'
        added.assert_not_called()


def test_job_api_preserves_actual_scope_and_exact_filed_identity(mod,monkeypatch,pdf_on_disk):
    from cps.services.reflow.ledger import Ledger
    _wire(mod,monkeypatch,pdf_on_disk)
    ledger=Ledger(os.path.join(mod.REFLOW_DIR,'jobs','5','a'*32+'.jsonl'),cap_usd=.5,job_id='a'*32)
    ledger.record({'kind':'job','event':'start','mode':'full','user_id':7,'cap_usd':.5})
    summary={'review_mode':'source_verified','eligible':5,'unreviewed':3,'approved_pages':1,'proposed_operations':2,'approved_operations':1}
    ledger.record({'kind':'structural_summary','summary':summary})
    ledger.record({'kind':'artifact','sha256':'b'*64,'bytes':1234})
    ledger.record({'kind':'job','event':'finish','status':'capped'})
    from types import SimpleNamespace
    with _ctx('/api/v1/books/5/reflow/jobs'),patch.object(mod,'current_user',_user()),patch.object(mod.WorkerThread,'get_instance',lambda:SimpleNamespace(tasks=[])):
        response=inspect.unwrap(mod.reflow_jobs)(5)
    row=_json(response)['items'][0]
    assert row['structural']==summary and row['artifact']=={'sha256':'b'*64,'bytes':1234}
    assert row['spend_usd']==row['pending_usd']==0 and row['cap_usd']==.5


def test_job_api_never_rounds_away_unresolved_liability(mod,monkeypatch,pdf_on_disk):
    from cps.services.reflow.ledger import Ledger
    from types import SimpleNamespace
    _wire(mod,monkeypatch,pdf_on_disk)
    ledger=Ledger(os.path.join(mod.REFLOW_DIR,'jobs','5','b'*32+'.jsonl'),cap_usd=1,job_id='b'*32)
    ledger.record({'kind':'job','event':'start','mode':'full','user_id':7,'cap_usd':1})
    bound=.000000123456789
    ledger.reserve_attempt('0',bound,model_id='inert/model')
    with _ctx('/api/v1/books/5/reflow/jobs'),patch.object(mod,'current_user',_user()),patch.object(mod.WorkerThread,'get_instance',lambda:SimpleNamespace(tasks=[])):
        row=_json(inspect.unwrap(mod.reflow_jobs)(5))['items'][0]
    assert row['pending_usd']==bound and row['spend_usd']==0
