"""Impossible local joins must not compete with a real source page boundary."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from cps.services.reflow import layout_ops, layout_requests, layout_wire, layout_model
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import boundary_source, answer

pytestmark = pytest.mark.unit


def test_no_local_wrap_cannot_offer_a_sentinel_join_beside_a_valid_boundary(boundary_source):
    """Drive a native PDF factory: reject the v7 failure while allowing its real boundary."""
    book, doc, sources, raws = boundary_source
    prepared = layout_ops.prepare(book, doc, 1, sources[1], raws[1],
        previous_source=sources[0], previous_raw=raws[0])
    view = prepared.model_view()
    assert view['wrap_lefts'] == [] and view['previous']['wrap_lefts'] and view['wrap_rights']
    response = answer(prepared)
    response.update(continuation=True, boundary_join=dict(
        left=view['previous']['wrap_lefts'][-1], right=view['wrap_rights'][0], hyphen='drop'), emphasis=[])
    validator = Draft202012Validator(layout_requests.response_schema(view))
    assert not list(validator.iter_errors(response)), 'real boundary must remain available'
    response['joins'] = [dict(left='NO_VALID_ID', right=view['wrap_rights'][0], hyphen='drop')]
    with pytest.raises(ContractError, match='join atom'):
        prepared.accept(book, doc, response, source_page=sources[1], raw_page=raws[1],
            previous_source=sources[0], previous_raw=raws[0])
    assert list(validator.iter_errors(response)), 'schema must reject the impossible local operation too'


@pytest.mark.parametrize('empty_side', ['wrap_lefts', 'wrap_rights'])
def test_missing_boundary_endpoint_allows_only_null(boundary_source, empty_side):
    book, doc, sources, raws = boundary_source
    prepared = layout_ops.prepare(book, doc, 1, sources[1], raws[1],
        previous_source=sources[0], previous_raw=raws[0])
    view = prepared.model_view()
    view['previous']['wrap_lefts'] = [] if empty_side == 'wrap_lefts' else view['previous']['wrap_lefts']
    if empty_side == 'wrap_rights':
        view['wrap_rights'] = []
    validator = Draft202012Validator(layout_requests.response_schema(view)['properties']['boundary_join'])
    assert not list(validator.iter_errors(None))
    assert list(validator.iter_errors(dict(left='NO_VALID_ID' if empty_side == 'wrap_lefts' else view['previous']['wrap_lefts'][0],
        right='NO_VALID_ID' if empty_side == 'wrap_rights' else view['wrap_rights'][0], hyphen='keep')))


def test_repeated_columns_fit_without_losing_absence_types_or_literal_source():
    """The same sparse token records must shrink enough for a complete neighbor view."""
    rows = [dict(id=f'a{i}', protected=False, text=f'literal {i} e\u0301 α') for i in range(200)]
    rows[3]['origin'] = dict(page=2, bbox=[1.1234567890123, 2, 3, 4], unknown=None)
    view = dict(atoms=rows, previous=dict(atoms=copy.deepcopy(rows)))
    original = copy.deepcopy(view)
    packed = layout_wire.pack(view)
    # Compare to the previous lossless common-field representation, not to product code.
    old_tables = {key: {'$layout_table': dict(fields=['id', 'protected', 'text'], rows=[
        [r['id'], r['protected'], r['text']] + ([{'origin': r['origin']}] if 'origin' in r else [])
        for r in rows])} for key in ('atoms',)}
    old_tables['previous'] = dict(atoms=old_tables['atoms'])
    assert len(layout_model._encoded(packed)) < len(layout_model._encoded(old_tables)) - 1400
    assert layout_model._encoded(layout_wire.unpack(packed)) == layout_model._encoded(original)
    assert view == original and 'origin' not in layout_wire.unpack(packed)['atoms'][4]
    mixed = [dict(value=v, stable=None) for v in (False, 0, True, 1)] * 20
    assert layout_model._encoded(layout_wire.unpack(layout_wire.pack(mixed))) == layout_model._encoded(mixed)


def test_table_defaults_reject_ambiguous_fields_and_keep_legacy_views_readable():
    legacy = dict(wire_format=layout_wire.LEGACY_VERSION, definition=layout_wire.LEGACY_DEFINITION,
        value_pool=[], view={'$layout_table': dict(fields=['id', 'protected'], rows=[['a0', False]])})
    assert layout_wire.source_view([dict(content=json.dumps(legacy))]) == [dict(id='a0', protected=False)]
    packed = layout_wire.pack([dict(id=f'a{i}', protected=False) for i in range(80)])
    table = packed['view']['$layout_table']
    assert layout_wire.unpack(packed)[-1] == dict(id='a79', protected=False)
    # A defaults key must not also consume a positional column. Extra keys can
    # override defaults but cannot overwrite an explicitly supplied column.
    for damage in ('column_default', 'column_extra', 'malformed_defaults', 'legacy_defaults'):
        changed = copy.deepcopy(packed)
        row = changed['view']['$layout_table']
        if damage == 'column_default': row['defaults'][row['fields'][0]] = 'forged'
        elif damage == 'column_extra': row['rows'][0].append({row['fields'][0]: 'forged'})
        elif damage == 'malformed_defaults': row['defaults'] = ['forged']
        else: changed.update(wire_format=layout_wire.LEGACY_VERSION, definition=layout_wire.LEGACY_DEFINITION)
        with pytest.raises(ValueError): layout_wire.unpack(changed)


def test_encoder_corruption_cannot_replace_the_source_message(monkeypatch):
    view = dict(atoms=[dict(id=f'a{i}', text='original source word', protected=False) for i in range(80)])
    message = dict(role='user', content=json.dumps(view))
    original = dict(message)
    pack = layout_wire.pack
    def corrupted(value):
        changed = copy.deepcopy(value)
        changed['atoms'][0]['text'] = 'fabricated source word'
        return pack(changed)
    monkeypatch.setattr(layout_wire, 'pack', corrupted)
    with pytest.raises(ValueError, match='logical source'):
        layout_wire.compact_message(message)
    assert message == original
