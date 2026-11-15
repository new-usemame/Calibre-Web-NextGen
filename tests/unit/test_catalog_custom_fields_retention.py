# SPDX-License-Identifier: GPL-3.0-or-later
"""Hidden display preferences survive visible edits without accepting new hidden fields."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from tests.unit.test_catalog_custom_fields import preferences as shared_preferences
from tests.unit.test_custom_column_sort import sortable_library as shared_sortable_library

preferences = shared_preferences
sortable_library = shared_sortable_library

pytestmark = pytest.mark.unit


@pytest.fixture
def live_preferences(preferences, monkeypatch):
    from cps import custom_column_sort, db
    account, users, post = preferences
    engine = create_engine("sqlite://")
    db.CustomColumns.__table__.create(engine)
    metadata = sessionmaker(bind=engine)()
    metadata.add_all([db.CustomColumns(id=i, name=name, datatype="int",
        is_multiple=False, mark_for_delete=False) for i, name in [(12, "Difficulty"), (13, "Score")]])
    metadata.commit()
    config = SimpleNamespace(config_sortable_custom_columns="12,13", config_columns_to_ignore="")
    monkeypatch.setattr(account, "config", config)
    monkeypatch.setattr(account, "load_configured_columns", custom_column_sort.load_configured_columns)
    monkeypatch.setattr(custom_column_sort, "calibre_db", SimpleNamespace(session=metadata))
    try:
        yield account, users, post, config, metadata
    finally:
        metadata.close()
        engine.dispose()


def seed(post):
    result, status = post({"custom_column_ids": [12, 13],
        "custom_column_labels": {"12": "Effort", "13": "Stars"}})
    assert status == 200
    return result


def test_hidden_choices_labels_survive_edit_and_unhide(live_preferences):
    account, users, post, config, _metadata = live_preferences
    seed(post)
    config.config_columns_to_ignore = "Difficulty"
    assert {c.id for c in account.load_configured_columns(config)} == {13}
    assert {c.id for c in account.load_configured_columns(config, include_hidden=True)} == {12, 13}
    result, status = post({"custom_column_ids": [13], "custom_column_labels": {"13": "New score"}})
    assert status == 200
    users.expire_all()
    assert set(result["custom_field_ids"]) == {12, 13}
    assert account.current_user.get_view_property("catalog", "custom_field_labels") == {
        "12": "Effort", "13": "New score"}
    config.config_columns_to_ignore = ""
    assert {c.id for c in account.load_configured_columns(config)} == {12, 13}
    assert set(account.current_user.get_view_property("catalog", "custom_field_ids")) == {12, 13}


def test_default_all_preserves_hidden_but_later_enabled_field_is_not_selected(live_preferences):
    account, users, post, config, metadata = live_preferences
    config.config_columns_to_ignore = "Difficulty"
    result, status = post({"custom_column_ids": [], "custom_column_labels": {}})
    assert status == 200 and result["custom_field_ids"] == [12]
    from cps import db
    metadata.add(db.CustomColumns(id=14, name="Later", datatype="float", is_multiple=False, mark_for_delete=False))
    metadata.commit()
    config.config_sortable_custom_columns = "12,13,14"
    result, status = post({"custom_column_ids": [], "custom_column_labels": {}})
    assert status == 200 and result["custom_field_ids"] == [12]
    users.expire_all()
    assert account.current_user.get_view_property("catalog", "custom_field_ids") == [12]


@pytest.mark.parametrize("change", ["removed", "unconfigured"])
def test_nonlive_hidden_choices_are_not_retained(live_preferences, change):
    _account, _users, post, config, metadata = live_preferences
    seed(post)
    config.config_columns_to_ignore = "Difficulty"
    if change == "removed":
        from cps import db
        metadata.query(db.CustomColumns).filter(db.CustomColumns.id == 12).delete()
        metadata.commit()
    else:
        config.config_sortable_custom_columns = "13"
    result, status = post({"custom_column_ids": [13], "custom_column_labels": {}})
    assert status == 200 and result == {"custom_field_ids": [13], "custom_field_labels": {}}


@pytest.mark.parametrize("payload", [
    {"custom_column_ids": [12]},
    {"custom_column_ids": [13], "custom_column_labels": {"12": "Forged"}},
])
def test_submitted_hidden_choice_or_label_is_refused(live_preferences, payload):
    account, users, post, config, _metadata = live_preferences
    original = seed(post)
    config.config_columns_to_ignore = "Difficulty"
    _result, status = post(payload)
    assert status == 400
    users.expire_all()
    assert account.current_user.view_settings["catalog"] == original


@pytest.mark.parametrize("count", [3, 2000])
def test_raw_duplicate_input_is_bounded_before_dedup(live_preferences, count):
    account, users, post, _config, _metadata = live_preferences
    _result, status = post({"custom_column_ids": [12] * count})
    assert status == 400
    users.expire_all()
    assert account.current_user.view_settings == {}


def test_bounded_duplicates_are_canonicalized(live_preferences):
    _account, _users, post, _config, _metadata = live_preferences
    result, status = post({"custom_column_ids": [12, 12]})
    assert status == 200 and result["custom_field_ids"] == [12]


@pytest.mark.parametrize("owner,expected_status", [(1, 409), (True, 400), ("2", 400), (None, 400)])
def test_queued_save_cannot_mutate_other_actual_user(live_preferences, monkeypatch, owner, expected_status):
    from cps import ub
    account, users, post, _config, _metadata = live_preferences
    original = seed(post)
    monkeypatch.setattr(account, "current_user", users.get(ub.User, 2))
    _result, status = post({"expected_user_id": owner, "custom_column_ids": [13]})
    assert status == expected_status
    users.expire_all()
    assert users.get(ub.User, 2).view_settings == {}
    assert users.get(ub.User, 1).view_settings["catalog"] == original


def test_matching_owner_and_legacy_payload_both_save(live_preferences):
    _account, _users, post, _config, _metadata = live_preferences
    assert post({"expected_user_id": 1, "custom_column_ids": [12]})[1] == 200
    assert post({"custom_column_ids": [13]})[1] == 200


@pytest.mark.parametrize("failure", ["commit", "metadata"])
def test_hidden_merge_failure_preserves_both_preferences(live_preferences, monkeypatch, failure):
    account, users, post, config, metadata = live_preferences
    original = seed(post)
    config.config_columns_to_ignore = "Difficulty"
    def error(*_args, **_kwargs):
        raise OperationalError("query", {}, RuntimeError("fixture"))
    if failure == "commit":
        monkeypatch.setattr(users, "commit", error)
    else:
        monkeypatch.setattr(metadata, "query", error)
    _result, status = post({"custom_column_ids": [13], "custom_column_labels": {"13": "Changed"}})
    assert status == (500 if failure == "commit" else 503)
    users.expire_all()
    assert account.current_user.view_settings["catalog"] == original


def test_download_custom_fallback_does_not_overwrite_stored_preference(live_preferences, sortable_library, monkeypatch):
    from cps import web
    account, users, _post, config, _metadata = live_preferences
    account.current_user.set_view_property("download", "stored", "cc-12-desc")
    monkeypatch.setattr(web, "config", config)
    monkeypatch.setattr(web, "current_user", account.current_user)
    monkeypatch.setattr(web, "render_downloaded_books", lambda _page, order, _id: order)
    result = web.render_books_list("download", "stored", 1, 1)
    assert result[1] == "new" and result[2] == ()
    users.expire_all()
    assert account.current_user.get_view_property("download", "stored") == "cc-12-desc"


def test_stale_visible_snapshot_preserves_newly_unhidden_choice_and_label(live_preferences):
    account, users, post, config, _metadata = live_preferences
    seed(post)
    config.config_columns_to_ignore = "Difficulty"
    known = [column.id for column in account.load_configured_columns(config)]
    assert known == [13]
    config.config_columns_to_ignore = ""
    result, status = post({"known_custom_column_ids": known, "custom_column_ids": [],
        "custom_column_labels": {"13": "New score"}})
    assert status == 200
    users.expire_all()
    assert result == {"custom_field_ids": [12],
                      "custom_field_labels": {"12": "Effort", "13": "New score"}}
    assert account.current_user.view_settings["catalog"] == result


@pytest.mark.parametrize("stored", ["bad", {"12": True}, [12, "13"], [12, True]])
def test_malformed_stored_selection_matches_me_default_all(live_preferences, monkeypatch, stored):
    import flask
    from cps.api import auth
    from cps.services.acquisition import admission
    account, users, post, config, _metadata = live_preferences
    account.current_user.set_view_property("catalog", "custom_field_ids", stored)
    monkeypatch.setattr(auth, "serialize_user", lambda _user: {})
    monkeypatch.setattr(auth, "_server_features", lambda: {})
    monkeypatch.setattr(auth, "_instance_name", lambda: "fixture")
    monkeypatch.setattr(auth, "_user_avatar", lambda _name: None)
    monkeypatch.setattr(admission, "instance_enabled", lambda _path: False)
    with flask.Flask(__name__).test_request_context():
        assert auth._me_payload(account.current_user)["catalog"]["custom_field_ids"] is None
    config.config_columns_to_ignore = "Score"
    result, status = post({"custom_column_ids": [], "known_custom_column_ids": [12]})
    assert status == 200 and result["custom_field_ids"] == [13]
    users.expire_all()
    assert account.current_user.get_view_property("catalog", "custom_field_ids") == [13]


@pytest.mark.parametrize("payload", [
    {"custom_column_ids": [True]}, {"custom_column_ids": ["12"]},
    {"custom_column_ids": [12], "known_custom_column_ids": [True]},
    {"custom_column_ids": [12], "known_custom_column_ids": "12"},
])
def test_malformed_id_arrays_refuse_before_metadata_outage(live_preferences, monkeypatch, payload):
    account, users, post, _config, metadata = live_preferences
    calls = []
    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise OperationalError("query", {}, RuntimeError("fixture outage"))
    monkeypatch.setattr(metadata, "query", unavailable)
    _result, status = post(payload)
    assert status == 400 and calls == []
    users.expire_all()
    assert account.current_user.view_settings == {}


@pytest.mark.parametrize("payload", [
    {"custom_column_ids": [], "known_custom_column_ids": [999]},
    {"custom_column_ids": [], "known_custom_column_ids": [12, 12, 12]},
    {"custom_column_ids": [12], "known_custom_column_ids": [13]},
    {"custom_column_ids": [13], "known_custom_column_ids": [13], "custom_column_labels": {"12": "Forged"}},
])
def test_invalid_or_outside_snapshot_input_is_refused(live_preferences, payload):
    account, users, post, _config, _metadata = live_preferences
    original = seed(post)
    _result, status = post(payload)
    assert status == 400
    users.expire_all()
    assert account.current_user.view_settings["catalog"] == original


def test_new_definition_outside_snapshot_does_not_auto_select_after_explicit_save(live_preferences):
    from cps import db
    _account, _users, post, config, metadata = live_preferences
    assert post({"custom_column_ids": [13]})[1] == 200
    metadata.add(db.CustomColumns(id=14, name="Later", datatype="float", is_multiple=False, mark_for_delete=False))
    metadata.commit()
    config.config_sortable_custom_columns = "12,13,14"
    result, status = post({"custom_column_ids": [], "known_custom_column_ids": [12, 13]})
    assert status == 200 and result["custom_field_ids"] == []
