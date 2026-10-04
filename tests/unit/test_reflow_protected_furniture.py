"""Opaque running furniture can move channels without losing its source tree."""
import copy

import pytest
from cps.services.reflow import _layout_atoms as atoms, layout_ops, layout_requests, layout_lexical
from cps.services.reflow.structural_ops import ContractError

pytestmark = pytest.mark.unit


def source_and_answer():
    source = atoms.prepare('<p>A printed sentence</p><figure id="header"><a href="original.xhtml#header"><img src="header.jpg" alt="Source header" /></a><figcaption>Source evidence</figcaption></figure><p>continues here.</p>', {}, 0)
    first, header, last = source['blocks']
    answer = dict(snapshot=source['snapshot'], joins=[], groups=[
        dict(role='paragraph', ranges=[first['range'], last['range']]),
        dict(role='source_furniture', ranges=[header['range']])])
    return source, answer


def test_raster_header_preserved_outside_continued_sentence():
    source, answer = source_and_answer()
    result = atoms.render(source, answer)
    groups = atoms.validate(source, answer)
    assert result['body'] == '<p>A printed sentence continues here.</p>'
    assert result['furniture'] == source['atoms'][3]['html']
    expected, verdicts = layout_ops._check_output(source, answer, groups, result)
    assert expected['body'] == 'A printed sentence continues here.'
    assert layout_requests._main_edge(groups, True) == groups[0][1]
    assert layout_lexical.edge(source, groups, False) == groups[0][1]
    broken = dict(result, furniture=result['furniture'].replace('header.jpg', 'other.jpg'))
    with pytest.raises(ContractError):
        layout_ops._check_output(source, answer, groups, broken)


@pytest.mark.parametrize('mutation', ['omit', 'duplicate', 'merge', 'inline'])
def test_retained_raster_disposition_keeps_exact_once_and_whole_block_gate(mutation):
    source, answer = source_and_answer()
    bad = copy.deepcopy(answer)
    if mutation == 'omit': bad['groups'].pop()
    elif mutation == 'duplicate': bad['groups'].append(copy.deepcopy(bad['groups'][-1]))
    elif mutation == 'merge': bad['groups'][-1]['ranges'].append(source['blocks'][0]['range'])
    else:
        source = atoms.prepare('<p>Word <img src="glyph.jpg" /> end.</p>', {}, 0)
        bad = dict(snapshot=source['snapshot'], joins=[], groups=[dict(role='source_furniture', ranges=[source['blocks'][0]['range']])])
    with pytest.raises(ContractError): atoms.render(source, bad)


def test_wire_schema_prevents_source_roles_on_inline_or_plain_atoms():
    from jsonschema import Draft202012Validator
    source, good = source_and_answer()
    source = atoms.model_view(source)
    schema = layout_requests.response_schema(source)
    validator = Draft202012Validator(schema)
    good.update(continuation=False, boundary_join=None, emphasis=[])
    assert validator.is_valid(good)
    for role in ('source', 'source_furniture'):
        for pair in (['a0', 'a0'], ['a3', 'a4']):
            bad = copy.deepcopy(good)
            bad['groups'][-1] = dict(role=role, ranges=[pair])
            assert not validator.is_valid(bad), (role, pair)
    plain = atoms.model_view(atoms.prepare('<p>Plain <img src="inline.png" /> prose.</p>', {}, 0))
    plain_schema = layout_requests.response_schema(plain)
    bad = dict(snapshot=plain['snapshot'], joins=[], emphasis=[], continuation=False, boundary_join=None,
               groups=[dict(role='source_furniture', ranges=[['a1', 'a1']])])
    assert not Draft202012Validator(plain_schema).is_valid(bad)


@pytest.mark.parametrize('fragment', [
    '<aside id="fn_1">1 A printed note.</aside>',
    '<table><tr><td>Printed</td><td>values</td></tr></table>',
    '<div class="source-evidence-notice">Source uncertainty.</div>',
])
def test_known_note_table_and_evidence_blocks_cannot_be_running_furniture(fragment):
    from jsonschema import Draft202012Validator
    source = atoms.prepare('<p>Ordinary source prose.</p>' + fragment, {}, 0)
    answer = dict(snapshot=source['snapshot'], joins=[], emphasis=[], continuation=False, boundary_join=None,
                  groups=[dict(role='paragraph', ranges=[source['blocks'][0]['range']]),
                          dict(role='source_furniture', ranges=[source['blocks'][1]['range']])])
    assert not Draft202012Validator(layout_requests.response_schema(atoms.model_view(source))).is_valid(answer)
    with pytest.raises(ContractError): atoms.render(source, answer)


def test_empty_source_can_emit_empty_groups_without_schema_failure():
    from jsonschema import Draft202012Validator
    source = atoms.model_view(atoms.prepare('', {}, 0))
    schema = layout_requests.response_schema(source)
    result = dict(snapshot=source['snapshot'], groups=[], joins=[], emphasis=[], continuation=False, boundary_join=None)
    assert Draft202012Validator(schema).is_valid(result)
