"""Checked continuations retain quote scope, source evidence and rejection gates."""
import json
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import build_epub, layout_ops as ops, layout_requests as requests, layout_lexical as lexical
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import boundary_source, answer
from tests.unit.test_reflow_structural_ops import texts, X
pytestmark = pytest.mark.unit


def plans_for(fixture, right_role='quote'):
    book, doc, sources, raws = fixture
    plans = []
    for page, role in enumerate(('quote', right_role)):
        context = dict(previous_source=sources[0], previous_raw=raws[0]) if page else {}
        prepared = ops.prepare(book, doc, page, sources[page], raws[page], **context)
        value = answer(prepared)
        value['groups'][0]['role'] = role
        if page:
            source = json.loads(prepared.contract_json)
            value.update(continuation=True, boundary_join=dict(
                left=source['previous']['wrap_lefts'][-1], right=source['wrap_rights'][0], hyphen='drop'))
        plans.append(prepared.accept(book, doc, value, source_page=sources[page], raw_page=raws[page], **context))
    return plans


def test_source_bound_quote_survives_review_lexical_and_final_publication(boundary_source, tmp_path):
    book, doc, sources, raws = boundary_source
    left, right = plans_for(boundary_source)
    binding, view, _, can_continue = requests._review_material(right.prepared, right, left)
    assert can_continue, 'A quote must have an eligible quote continuation, just like prose'
    refused = requests.validate_review(right.prepared, right, dict(snapshot=binding,
        accept=True, continuation_accept=False, problems=[]), left).accepted_plan
    assert json.loads(refused.answer_json)['continuation'] is False
    with pytest.raises(ContractError):
        requests.validate_review(right.prepared, right, dict(snapshot=binding,
            accept=True, continuation_accept=True, problems=[]), None)
    selected = requests.validate_review(right.prepared, right, dict(snapshot=binding,
        accept=True, continuation_accept=True, problems=[]), left).accepted_plan
    rows = lexical.candidates([left, selected])
    assert any(row['scope'] == 'page_boundary' for row in rows)
    result = build_epub.build(book, str(tmp_path/'quote.epub'), doc=doc,
        source_pages=dict(enumerate(sources)), raw_pages=dict(enumerate(raws)), layout_plans=[left, selected])
    assert build_epub.validate(result.path) == []
    quotes = [el for root in texts(result.path) for el in root.iter(X+'blockquote')]
    assert len(quotes) == 1
    assert 'Another cooperation resumes' in ''.join(quotes[0].itertext())
    assert any(el.get('id') == 'source_return_0001' for el in quotes[0].iter())
    assert result.page_joins == 1


@pytest.mark.parametrize('role', ['paragraph', 'heading2', 'lineblock'])
def test_quote_boundary_cannot_cross_role_or_heading(boundary_source, role):
    book, doc, sources, raws = boundary_source
    left, right = plans_for(boundary_source, role)
    assert not requests._review_material(right.prepared, right, left)[3]
    assert not any(row['scope'] == 'page_boundary' for row in lexical.candidates([left, right]))
    with pytest.raises(ContractError):
        ops.compile_boundary(left, right, book, doc, left_source=sources[0], right_source=sources[1],
            left_raw=raws[0], right_raw=raws[1])


@pytest.mark.parametrize('tag', ['p', 'div'])
@pytest.mark.parametrize('notice', ['source-evidence-notice', 'source-check-notice'])
def test_checked_flow_skips_but_preserves_both_source_notice_forms(tag, notice):
    evidence = f'<{tag} class="{notice}"><a href="original.xhtml">Evidence</a></{tag}>'
    furniture = '<section class="reflow-retained-furniture"><p>Running head</p></section>'
    pages = [dict(pno=0, body=['<p>A source sentence</p>', evidence, furniture], asides=['<aside>Note</aside>'], anchor=''),
             dict(pno=1, body=[evidence, '<p>continues here.</p>'], asides=[], anchor='<span id="pg_0001"></span>')]
    assert build_epub._join_page_turns(pages, layout_pages={0,1}, layout_boundaries={1:dict(hyphen=None, role='paragraph')}) == 1
    assert ''.join(ET.fromstring(pages[0]['body'][0]).itertext()) == 'A source sentence continues here.'
    assert pages[0]['body'][1:] == [evidence, furniture] and pages[1]['body'] == [evidence]
    assert pages[0]['asides'] == ['<aside>Note</aside>']


def test_opaque_printed_note_list_is_explicit_exact_and_never_guessed():
    from cps.services.reflow import _layout_atoms as atoms
    source = atoms.prepare('<p>Body text.</p><ol class="source-list"><li>1. Exact citation.</li><li>2. Another citation.</li></ol>', 'fixture', 0)
    value = dict(snapshot=source['snapshot'], joins=[], groups=[
        dict(role='paragraph', ranges=[source['blocks'][0]['range']]),
        dict(role='note', ranges=[source['blocks'][1]['range']])])
    from jsonschema import Draft202012Validator
    value.update(continuation=False, boundary_join=None, emphasis=[])
    assert Draft202012Validator(requests.response_schema(atoms.model_view(source))).is_valid(value)
    groups = atoms.validate(source, value)
    rendered = ops._render(source, value)
    expected, gates = ops._check_output(source, value, groups, rendered)
    root = ops._tree(rendered['body'])
    assert root[1].tag == 'aside' and root[1][0].tag == 'ol'
    assert ET.tostring(root[1][0], encoding='unicode') == source['atoms'][-1]['html']
    assert not list(root[1].iter('a')), 'Unbound note placement must not invent associations'
    value['groups'][1]['role'] = 'source'
    groups = atoms.validate(source, value)
    assert requests._main_edge(groups, True) is None, 'An unselected list remains a main-flow barrier'
    value['groups'][1]['role'] = 'note'
    groups = atoms.validate(source, value)
    damaged = dict(rendered, body=rendered['body'].replace('<li>2.', '<li class="changed">2.'))
    with pytest.raises(ContractError): ops._check_output(source, value, groups, damaged)
    # Known figures and headings are not candidates for opaque note placement.
    for fragment in ['<figure><img src="figure.jpg"/></figure>', '<h2 id="printed-heading"><img src="glyph.jpg"/>Title</h2>']:
        barrier = atoms.prepare(fragment, 'fixture-barrier', 0)
        proposed = dict(snapshot=barrier['snapshot'], joins=[], groups=[dict(role='note', ranges=[barrier['blocks'][0]['range']])])
        with pytest.raises(ContractError): atoms.validate(barrier, proposed)
