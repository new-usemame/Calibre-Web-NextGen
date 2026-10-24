# -*- coding: utf-8 -*-
"""Metadata clean-up admin API (#2438, Discussion #2481): access, rules, preview, apply, undo.

The routes in cps/api/value_maps.py over a real SQLite library and app.db
(tests/unit/value_map_world.py).
"""
import pytest

import cps.api.value_maps as routes
from tests.unit.value_map_world import ADMIN, NOBODY, READER, app, app_db, call, library  # noqa: F401

pytestmark = pytest.mark.unit

RULES = {"rules": [
    {"incoming": "Fiction / Thrillers", "targets": ["Thriller"]},
    {"incoming": "General", "targets": []},
]}


@pytest.mark.parametrize("user, status", [(NOBODY, 401), (READER, 403)])
def test_only_admins_reach_the_map(call, user, status):
    assert call(routes.value_map_state, "tags", user=user)[0] == status
    assert call(routes.value_map_replace, "tags", user=user, method="PUT", json=RULES)[0] == status
    assert call(routes.change_set_apply, 1, user=user, method="POST", json={})[0] == status


def test_unknown_field_is_not_found(call):
    assert call(routes.value_map_state, "series")[0] == 404


def test_state_lists_the_librarys_values_and_an_empty_map(call):
    status, body = call(routes.value_map_state, "tags")
    assert status == 200
    assert body["rules"] == [] and body["changes"] == [] and body["history"] == []
    assert {v["name"]: v["count"] for v in body["values"]} == {
        "Fiction / Thrillers": 1, "thriller": 1, "General": 1, "Dragons": 1}


def test_invalid_rules_are_refused_with_every_reason(call):
    status, body = call(routes.value_map_replace, "tags", method="PUT", json={"rules": [
        {"incoming": "", "targets": []},
        {"incoming": "A, B", "targets": ["A"]},
    ]})
    assert status == 400
    assert len(body["error"]["details"]) == 2
    assert call(routes.value_map_state, "tags")[1]["rules"] == []


def test_saving_rules_previews_without_writing(call, library):
    status, body = call(routes.value_map_replace, "tags", method="PUT", json=RULES)
    assert status == 200
    assert [(c["book_id"], c["before"], c["after"]) for c in body["changes"]] == [
        (1, ["Fiction / Thrillers", "General"], ["Thriller"]),
        (2, ["thriller"], ["Thriller"]),
    ]
    library.expire_all()
    assert library.get(routes.db.Books, 1).tags[0].name == "Fiction / Thrillers"


def test_preview_with_nothing_to_change_is_a_conflict(call):
    assert call(routes.value_map_preview, "tags", method="POST")[0] == 409


def test_apply_reports_each_book_and_undo_restores(call, library):
    call(routes.value_map_replace, "tags", method="PUT", json=RULES)
    status, proposed = call(routes.value_map_preview, "tags", method="POST")
    assert status == 200 and proposed["status"] == "proposed"

    status, result = call(routes.change_set_apply, proposed["id"], method="POST",
                          json={"exclude_book_ids": [2]})
    assert status == 200
    assert result["written"] == 1 and result["files_rewritten"] is True
    assert result["set"]["counts"] == {"applied": 1, "skipped": 1}
    assert [b for b, _ in call.queued] == [1]
    library.expire_all()
    assert [t.name for t in library.get(routes.db.Books, 1).tags] == ["Thriller"]
    assert [h["status"] for h in result["state"]["history"]] == ["applied"]

    # An applied set cannot be applied again.
    assert call(routes.change_set_apply, proposed["id"], method="POST", json={})[0] == 409

    status, result = call(routes.change_set_undo, proposed["id"], method="POST", json={})
    assert status == 200 and result["set"]["counts"] == {"undone": 1, "skipped": 1}
    library.expire_all()
    assert sorted(t.name for t in library.get(routes.db.Books, 1).tags) == ["Fiction / Thrillers", "General"]


def test_apply_refuses_a_malformed_exclusion_list(call):
    call(routes.value_map_replace, "tags", method="PUT", json=RULES)
    proposed = call(routes.value_map_preview, "tags", method="POST")[1]
    assert call(routes.change_set_apply, proposed["id"], method="POST",
                json={"exclude_book_ids": ["2"]})[0] == 400


def test_an_unknown_change_set_is_not_found(call):
    assert call(routes.change_set_detail, 999)[0] == 404
