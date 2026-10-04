"""Optional styling cannot grant or destroy independently checked structure."""
import copy
import json

import pytest
from cps.services.reflow import layout_pipeline as layout, pipeline
from cps.services.reflow.ledger import Ledger
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result
from tests.unit.test_reflow_layout_ops import prepared, answer
from tests.unit.test_reflow_layout_transport import Session

pytestmark = pytest.mark.unit


class Channels(Session):
    def __init__(self, fixture, style='invalid', damage=None):
        super().__init__()
        self.style, self.damage = style, damage
        self.value = prepared(fixture)[-1]
        self.proposal = answer(self.value)
        self.proposal.update(continuation=False, boundary_join=None, emphasis=[])
        self.proposal['groups'][0]['role'] = 'heading2'
        source = json.loads(self.value.contract_json)
        self.label = next(a['id'] for a in source['atoms'] if a['text'] == 'Following')
        self.proposal['emphasis'] = [['a0', 'a0']] if style == 'invalid' else [[self.label, self.label]]
        self.route.update(context_length=1050000, max_prompt_tokens=922000)

    def post(self, *args, **kwargs):
        wire = json.loads(kwargs['data'])
        identity = json.loads(wire['messages'][1]['content'])
        from cps.services.reflow import layout_wire,layout_ranges
        view = layout_wire.source_view(wire['messages'][:-1])
        if identity['stage'] == 'proposer':
            from tests.unit.test_reflow_range_choices import range_fixture
            response = range_fixture(self.value,self.proposal['groups'],emphasis=[[int(x[1:]) for x in pair] for pair in self.proposal['emphasis']])
            if self.damage == 'omit': response['groups'][0]['ranges'][0][1]-=1
            if self.damage == 'duplicate':
                response['groups'][0].update(order='explicit',atoms=[0,0])
            if self.damage == 'stale': response['snapshot'] = 'stale'
        elif identity.get('context', {}).get('decision_channel') == 'emphasis':
            accepted = self.style == 'approved'
            response = dict(snapshot=view['snapshot'], accept=accepted,
                            problems=[] if accepted else ['unsupported_change'])
            if self.style == 'stale_review': response['snapshot'] = 'stale'
        else:
            assert 'emphasis' not in view  # Styling does not contaminate the structural verdict.
            response = dict(snapshot=view['snapshot'], accept=self.damage != 'semantic',
                            continuation_accept=False,
                            problems=['paragraph_split'] if self.damage == 'semantic' else [])
        if 'decisions' in view:response['decisions']='1'*layout_ranges.decision_count(view['decisions'])
        self.data['choices'][0]['message']['content'] = json.dumps(response)
        if self.damage == 'truncated': self.data['choices'][0]['finish_reason'] = 'length'
        return super().post(*args, **kwargs)


def run(fixture, session):
    _, doc, tmp = fixture
    return layout.run_layout(doc, client=layout.LayoutClient('fixture', enabled=True, session=session),
        cache=pipeline.PageCache(tmp/'cache'), ledger=Ledger(str(tmp/'ledger'), cap_usd=1),
        prepared_result=prepared_result(fixture))


def test_invalid_optional_emphasis_still_requires_independent_structure_review(source):
    session = Channels(source)
    result = run(source, session)
    assert len(result.layout_plans) == 1, result.structural
    assert len(session.posts) == 2
    plan = result.layout_plans[0]
    assert json.loads(plan.answer_json)['groups'] == session.proposal['groups']
    assert not json.loads(plan.answer_json).get('emphasis')
    decision = result.structural['emphasis'][0]
    assert decision['status'] == 'rejected' and 'prose group' in decision['reason']
    assert decision['requested'] == session.proposal['emphasis']
    assert decision['proposal_sha256']


@pytest.mark.parametrize('style', ['approved', 'semantic_rejection', 'stale_review'])
def test_valid_optional_emphasis_has_its_own_bound_semantic_gate(source, style):
    session = Channels(source, style)
    result = run(source, session)
    assert len(result.layout_plans) == 1, result.structural
    assert len(session.posts) == 3
    selected = json.loads(result.layout_plans[0].answer_json)
    assert selected['groups'] == session.proposal['groups']
    assert bool(selected.get('emphasis')) == (style == 'approved')
    assert result.structural['emphasis'][0]['status'] == ('approved' if style == 'approved' else 'rejected')


@pytest.mark.parametrize('damage', ['omit', 'duplicate', 'stale', 'truncated', 'semantic'])
def test_style_channel_cannot_rescue_invalid_or_unreviewed_structure(source, damage):
    result = run(source, Channels(source, damage=damage))
    assert not result.layout_plans


def test_model_constraints_are_existing_protected_flow_contracts():
    from cps.services.reflow import _layout_atoms as atoms
    s = atoms.prepare('<p>A <sup>4</sup> remains.</p>'
        '<ol class="source-list"><li>4. Unbound citation.</li></ol>'
        '<p class="source-evidence-notice"><a href="original.xhtml">View source</a></p>'
        '<figure><img src="whole.jpg"/><figcaption>Actual caption</figcaption></figure>', 'source', 0)
    view = atoms.model_view(s)
    constraints = {v['atom']: v for v in view['protected_constraints']}
    for block in s['blocks']:
        if not block['opaque']: continue
        row = constraints[block['range'][0]]
        assert ('note' in row['allowed_roles']) == atoms.can_be_source_note(block)
        assert ('source_furniture' in row['allowed_roles']) == atoms.can_be_source_furniture(block)
    note = constraints[s['blocks'][1]['range'][0]]
    assert note['binding_authority'] == 'none' and note['flow_by_role']['source'] == 'barrier'
    assert note['flow_by_role']['note'] == 'separate'
    notice = constraints[s['blocks'][2]['range'][0]]
    assert notice['flow_by_role'] == {'source': 'separate'}
    assert constraints['a1']['allowed_roles'] == sorted(atoms.ROLES - {'source', 'source_furniture', 'furniture'})


def test_consent_ceiling_includes_both_independent_review_channels(source):
    from cps.services.reflow import layout_quote, layout_requests, layout_emphasis
    book, doc, page, raw, value = prepared(source)
    measured = layout_quote.measure(doc, prepared_result=prepared_result(source))
    row = measured['pages'][0]
    session = Channels(source, 'approved')
    structural = dict(session.proposal); requested = structural.pop('emphasis')
    plan = value.accept(book, doc, structural, source_page=page, raw_page=raw)
    reviewer = layout.LayoutClient('').stages['reviewer']
    structure_wire = reviewer.prepare_request(layout_requests.review(value, plan), value.raster)
    style_wire = reviewer.prepare_request(layout_emphasis.review(plan, requested), value.raster)
    assert row['verifier_bound_usd'] == 2 * layout_quote._maximum('reviewer')['bound_usd']
    assert row['verifier_bound_usd'] >= structure_wire.bound_usd + style_wire.bound_usd
    assert row['verifier_max_requests'] == 2
    assert layout_quote.assert_request_bound(measured, 'reviewer', style_wire, 0)


@pytest.mark.parametrize('finish,content,reason', [
    ('length', '', 'completion_limit'),
    ('length', '{"accept":true,"snapshot":"unfinished', 'completion_limit'),
    ('stop', '{broken', 'malformed_json')])
def test_completion_exhaustion_is_distinct_from_malformed_json_and_remains_refused(tmp_path, finish, content, reason):
    from cps.services.reflow import layout_model, model
    from tests.unit.test_reflow_layout_transport import envelope
    session = Session()
    session.data['choices'][0].update(finish_reason=finish, message={'content': content})
    transport = layout_model.LayoutStageClient('fixture', 'proposer', session=session)
    ledger = Ledger(str(tmp_path/'receipt'), cap_usd=1)
    with pytest.raises(model.ModelError) as caught:
        transport.call(transport.prepare_request(envelope()), ledger=ledger)
    assert caught.value.reason_code == reason
    assert ledger.pending_usd() == 0 and ledger.spent() == session.data['usage']['cost']
    assert ledger.entries('attempt_diagnostic')[-1]['reason_code'] == reason
