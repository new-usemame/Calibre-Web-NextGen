from datetime import datetime, timezone

import pytest

from cps import magic_shelf


@pytest.mark.parametrize("value", [None, "", 0, -1, True, [], {}, "nope", 36501])
def test_relative_date_rejects_empty_wrong_type_and_out_of_range(value):
    rule = {"id": "timestamp", "operator": "in_last_days", "value": value}
    assert magic_shelf.build_filter_from_rule(rule) is None


def test_relative_date_only_applies_to_date_fields():
    assert magic_shelf.build_filter_from_rule(
        {"id": "title", "operator": "in_last_days", "value": 28}
    ) is None


def test_relative_date_builds_live_threshold_expression():
    expr = magic_shelf.build_filter_from_rule(
        {"id": "timestamp", "operator": "in_last_days", "value": "28"}
    )
    assert expr is not None
    assert "timestamp" in str(expr)
    params = list(expr.compile().params.values())
    assert len(params) == 1
    threshold = params[0]
    age = datetime.now(timezone.utc).replace(tzinfo=None) - threshold
    assert 27 <= age.days <= 28


def test_relative_date_negation_is_supported():
    expr = magic_shelf.build_filter_from_rule(
        {"id": "pubdate", "operator": "not_in_last_days", "value": 180}
    )
    assert expr is not None
    assert "pubdate" in str(expr)
