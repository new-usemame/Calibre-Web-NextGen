"""Typed formatting remains a separately tested library, outside app conversion."""
import copy
import hashlib
import zipfile

import pytest

from cps.services.reflow import build_epub, operation_audit, pipeline
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.structural_pipeline import TwoStageClient, run_structural
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result

class _ChoicesSession:
    """Inert provider boundary; real source choices, ledger, native child and compiler."""
    def __init__(self, decision='approve', error=None, malformed=False):
        from tests.unit.test_reflow_typed_transport import Session
        self.transport=Session(); self.decision=decision; self.error=error
        self.malformed=malformed; self.calls=[]; self.proposals={}

    def get(self,*args,**kwargs): return self.transport.get(*args,**kwargs)

    def post(self,*args,**kwargs):
        import json
        from tests.unit.test_reflow_typed_transport import reply, Session
        from cps.services.reflow.structural_quote import maximum_legal_ids
        wire=json.loads(kwargs['data']); self.calls.append(wire)
        if self.error: raise self.error
        body=json.loads(wire['messages'][1]['content'][1]['text'])
        response=dict(body['empty_response']); source=body['source']
        if self.malformed or (self.decision=='partial' and 'select' in response and self.proposals):
            content='invalid source-choice response'
        else:
            if 'select' in response:
                response['select']=maximum_legal_ids([candidate for candidate in source['candidates'] if candidate['kind']=='quote'])
                self.proposals[source['snapshot_id']]=response
            else:
                response['approve']=[] if self.decision=='decline' else source['verification']['proposed_ids']
                if self.decision=='stale': response['snapshot_id']='0'*64
            content=json.dumps(response)
        data=reply(content); data['model']=wire['model']
        return Session(data).post()


@pytest.mark.parametrize('decision', ['decline', 'approve', 'stale', 'audit_failure', 'partial'])
def test_typed_library_only_emits_the_verified_operation_subset(source, monkeypatch, decision):
    book, doc, directory = source
    if decision=='partial':
        doc.fullcopy_page(0)
        doc.saveIncr()
        elements=copy.deepcopy(book.pages[0])
        for element in elements:element.pno=1
        book.pages[1]=elements;book.elements.extend(elements)
        notes=copy.deepcopy(book.notes)
        for note in notes:note.pno=1
        book.notes.extend(notes)
        figures=copy.deepcopy(book.figures)
        for figure in figures:figure['pno']=1
        book.figures.extend(figures)
    prepared=prepared_result(source)
    if decision=='partial':
        from cps.services.reflow import extract
        prepared.raw_pages.append(extract.read_page(doc,1))
        prepared.page_html[1]=build_epub.page_fragment(book,1)
    session = _ChoicesSession(decision=decision)
    audit = Ledger(directory / 'typed-library.jsonl', cap_usd=1)
    result = run_structural(doc, client=TwoStageClient('inert', enabled=True, session=session),
        ledger=audit, cache=pipeline.PageCache(directory / 'typed-cache'),
        prepared_result=prepared)
    approve = decision in ('approve', 'audit_failure', 'partial')
    expected_calls=3 if decision=='partial' else 2
    assert len(session.calls) == expected_calls
    assert result.structural['proposed_pages'] == 1
    assert result.structural['approved_pages'] == int(approve)
    proposed = next(iter(session.proposals.values()))
    assert result.structural['approved_operations'] == (len(proposed['select']) if approve else 0)
    assert result.structural['rejected'] == int(decision in ('stale','partial'))
    assert audit.spent() == pytest.approx(expected_calls * .000123456789)
    receipts = [entry for entry in audit.entries('reservation') if entry['event'] == 'reconciled']
    assert len(receipts) == len(session.calls) and audit.pending_usd() == 0
    target = directory / 'typed-result.epub'
    if decision == 'audit_failure':
        real = audit.record
        def fail(entry, **kwargs):
            if entry.get('kind') == 'operation_audit':
                raise OSError('Synthetic required audit failure.')
            return real(entry, **kwargs)
        monkeypatch.setattr(audit, 'record', fail)
        with pytest.raises(OSError, match='required audit'):
            operation_audit.record(audit, 'validated', operations=[])
        assert not target.exists()
        monkeypatch.setattr(audit,'record',real)
        other=Ledger(directory/'second-owner.jsonl',cap_usd=1)
        replay=run_structural(doc,client=TwoStageClient('inert',enabled=True,session=session),
            ledger=other,cache=pipeline.PageCache(directory/'typed-cache'),
            prepared_result=prepared_result(source))
        assert len(session.calls)==expected_calls and other.spent()==0
        assert replay.structural['approved_operations']==result.structural['approved_operations']
        assert replay.structural['cached_stages']==2
        return
    rows,matching=operation_audit.capture(result,5,document=doc)
    operation_audit.record(audit,'validated',operations=rows)
    build_epub.build(book, str(target), doc=doc, page_html=result.page_html,
        source_pages=result.source_pages, operation_plans=result.operation_plans)
    assert build_epub.validate(str(target)) == []
    observed=operation_audit.observe(target,rows,matching)
    operation_audit.record(audit,'emitted',operations=observed,
                          sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    trace=audit.entries('operation_audit')
    assert [entry['event'] for entry in trace]==['validated','emitted']
    assert len(rows)==len(observed)==(len(proposed['select']) if approve else 0)
    if approve:
        quote=next(row for row in rows if row['kind']=='quote')
        assert len(quote['selected_text_sha256'])==len(quote['raster_sha256'])==64
        assert len(quote['requests'])==2
        assert {request['stage'] for request in quote['requests']}=={'proposer','verifier'}
        assert all(row['status']=='verified' and row['epub_entry'].startswith('OEBPS/ch')
                   for row in observed), observed
    assert trace[1]['sha256']==hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        body = ''.join(archive.read(name).decode() for name in archive.namelist()
            if '/ch' in name and name.endswith('.xhtml'))
    # The source fixture's displayed-quotation role change must agree
    # with the verified plan, including refusal of a stale snapshot.
    assert ('<blockquote' in body) == approve
    assert len(result.operation_plans) == int(approve)


def test_typed_library_disabled_quality_gate_never_calls_the_provider(source):
    _, doc, directory = source
    session = _ChoicesSession()
    result = run_structural(doc, client=TwoStageClient('inert', enabled=False, session=session),
        ledger=Ledger(directory / 'disabled.jsonl', cap_usd=1),
        cache=pipeline.PageCache(directory / 'typed-cache'),
        prepared_result=prepared_result(source))
    assert session.calls == []
    assert result.stopped == 'quality_gate'
    assert result.structural['approved_operations'] == 0
    assert result.structural['unreviewed'] == result.structural['eligible']
    assert result.spend_usd == 0
