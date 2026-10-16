"""Product conversion asks only for source-bound wrapper choices."""
import json
import zipfile
import pytest
from tests.unit.test_reflow_task import rig, _run
from tests.unit.test_reflow_typed_transport import Session, reply
from cps.services.reflow import structural_pipeline, structural_quote

pytestmark = pytest.mark.unit


class ChoicesSession:
    def __init__(self): self.calls = []
    def get(self, *args, **kwargs): return Session().get(*args, **kwargs)
    def post(self, *args, **kwargs):
        payload = json.loads(kwargs['data']); self.calls.append(payload)
        body = json.loads(payload['messages'][1]['content'][1]['text'])
        response = dict(body['empty_response'])
        if 'select' in response:
            response['select'] = [c['candidate_id'] for c in body['source']['candidates'] if c['kind']=='quote']
        else:
            response['approve'] = body['source']['verification']['proposed_ids']
        data = reply(json.dumps(response)); data['model'] = payload['model']
        return Session(data).post()


def test_product_client_exposes_only_candidate_selection_stages(rig):
    client = rig.mod.make_client('source_verified')
    assert isinstance(client, structural_pipeline.TwoStageClient)
    assert set(client.stages) == {'proposer', 'verifier'}


def test_product_sample_adopts_complete_source_quote_without_layout_generation(rig, monkeypatch):
    session = ChoicesSession()
    client = structural_pipeline.TwoStageClient('inert', session=session)
    monkeypatch.setattr(rig.mod, 'make_client', lambda mode: client)
    task = _run(rig, mode='sample', sample_pages=3, cost_cap_usd=1)
    from cps.services.worker import STAT_FINISH_SUCCESS
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert len(session.calls) == 2
    assert task.results['report']['structural']['approved_quote']==1,task.results['report']['structural']
    assert not rig.local_db.session.commits
    with zipfile.ZipFile(task.results['path']) as archive:
        chapters = [archive.read(n).decode() for n in archive.namelist()
                    if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
    assert any('<blockquote' in text and 'A separately displayed source quotation remains complete.' in text
               for text in chapters)
    for payload in session.calls:
        body = json.loads(payload['messages'][1]['content'][1]['text'])
        assert set(body['empty_response']) <= {'protocol', 'snapshot_id', 'proposal_id', 'select', 'approve'}


def test_clear_prose_product_conversion_never_dispatches_or_reserves(rig, monkeypatch):
    import pymupdf
    doc=pymupdf.open()
    for index in range(3):
        page=doc.new_page(width=500,height=700)
        for y in (100,115,130):
            page.insert_text((50,y), 'Ordinary source paragraphs keep their printed words.', fontsize=12)
    doc.save(str(rig.folder/'Book - Author.pdf'));doc.close()
    session=ChoicesSession()
    monkeypatch.setattr(rig.mod,'make_client',lambda mode:structural_pipeline.TwoStageClient('inert',session=session))
    task=_run(rig,mode='full',cost_cap_usd=1)
    from cps.services.worker import STAT_FINISH_SUCCESS
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert session.calls==[]
    assert task.results['report']['fidelity']['pages_routed']==0
    assert task.results['report']['spend']['usd']==0


def test_product_consent_binds_actual_source_choice_requests(rig, monkeypatch):
    import pymupdf
    with pymupdf.open(str(rig.folder/'Book - Author.pdf')) as doc:
        quote=structural_quote.measure(doc,recovery_opts={'mode':'off'})
    session=ChoicesSession()
    monkeypatch.setattr(rig.mod,'make_client',lambda mode:structural_pipeline.TwoStageClient('inert',session=session))
    options=rig.mod.ReflowOptions(dict(mode='sample',sample_pages=3,review_mode='source_verified',
                                     source_recovery='off',cost_cap_usd=1))
    options.consent_quote=quote
    task=rig.mod.TaskReflowPdf(5,7,options);task.run(None)
    from cps.services.worker import STAT_FINISH_SUCCESS
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert len(session.calls)==2
    assert task.results['report']['structural']['approved_quote']==1,task.results['report']['structural']
