# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Named per-user boolean preferences stored in User.view_settings.

A null value in /me means "not adopted yet"; true/false is authoritative
server state for all catalog-wide account preferences.
"""
import inspect
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONTEND = _ROOT / "frontend" / "src"
_UNSET_PREFERENCES = {
    "discover_hidden": None,
    "show_hidden_books": None,
    "card_actions_hidden": None,
}


def _serializable_user(view_settings):
    from cps import constants, ub

    user = ub.User()
    user.id = 7
    user.name = "reader"
    user.locale = "en"
    user.theme = 1
    user.ui_font_body = ""
    user.ui_font_display = ""
    user.role = constants.ROLE_USER
    user.sidebar_view = constants.ADMIN_USER_SIDEBAR
    user.view_settings = view_settings
    user.kobo_only_shelves_sync = 0
    return user


def test_me_serializes_named_preference_and_unset_state():
    from cps.api.serializers import serialize_user

    assert serialize_user(_serializable_user({}))["preferences"] == \
        _UNSET_PREFERENCES
    assert serialize_user(_serializable_user({
        "preferences": {
            "discover_hidden": True,
            "show_hidden_books": False,
            "card_actions_hidden": True,
        },
    }))["preferences"] == {
        "discover_hidden": True,
        "show_hidden_books": False,
        "card_actions_hidden": True,
    }


def test_me_ignores_malformed_stored_preference():
    from cps.api.serializers import serialize_user

    payload = serialize_user(_serializable_user({
        "preferences": {"discover_hidden": "yes"},
    }))
    assert payload["preferences"] == _UNSET_PREFERENCES


@pytest.mark.parametrize("user", [
    object(),
    SimpleNamespace(get_view_property=MagicMock(
        side_effect=RuntimeError("legacy JSON is unreadable"))),
])
def test_serializer_degrades_when_preference_store_is_missing_or_raises(user):
    from cps.user_preferences import serialize_named_preferences

    assert serialize_named_preferences(user) == _UNSET_PREFERENCES


class _FakeUser:
    def __init__(self, *, anonymous=False, view_settings=None):
        self.is_authenticated = not anonymous
        self.is_anonymous = anonymous
        self.view_settings = view_settings if view_settings is not None else {}

    def get_view_property(self, page, prop):
        section = self.view_settings.get(page)
        return section.get(prop) if isinstance(section, dict) else None

    def set_view_property(self, page, prop, value, commit=True):
        assert commit is False, "the endpoint must own the transaction"
        self.view_settings.setdefault(page, {})[prop] = value


def _ctx(body):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(
        "/api/v1/account/preferences", method="POST", json=body,
        content_type="application/json",
    )


def _call(body, user, session=None):
    from cps.api import account

    session = session or MagicMock()
    with _ctx(body), \
         patch.object(account, "current_user", user), \
         patch.object(account.ub, "session", session):
        response = inspect.unwrap(account.update_named_preferences)()
    return response, session


def _status(response):
    return response[1] if isinstance(response, tuple) else response.status_code


def _json(response):
    response = response[0] if isinstance(response, tuple) else response
    return json.loads(response.get_data(as_text=True))


@pytest.mark.parametrize("name", _UNSET_PREFERENCES)
def test_endpoint_persists_each_known_boolean_and_returns_state(name):
    user = _FakeUser()
    response, session = _call({"preferences": {name: True}}, user)

    assert _status(response) == 200
    assert user.view_settings == {"preferences": {name: True}}
    expected = {**_UNSET_PREFERENCES, name: True}
    assert _json(response) == {"preferences": expected}
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_endpoint_updates_multiple_preferences_in_one_transaction():
    user = _FakeUser()
    updates = {
        "discover_hidden": True,
        "show_hidden_books": True,
        "card_actions_hidden": False,
    }
    response, session = _call({"preferences": updates}, user)

    assert _status(response) == 200
    assert user.view_settings == {"preferences": updates}
    assert _json(response) == {"preferences": updates}
    session.commit.assert_called_once_with()


@pytest.mark.parametrize("body", [
    {},
    {"preferences": {}},
    {"preferences": []},
    {"preferences": {"unknown": True}},
    {"preferences": {"discover_hidden": 1}},
    {"preferences": {"discover_hidden": "true"}},
])
def test_endpoint_rejects_invalid_updates_without_mutating(body):
    user = _FakeUser()
    response, session = _call(body, user)

    assert _status(response) == 400
    assert user.view_settings == {}
    session.commit.assert_not_called()


def test_endpoint_rolls_back_commit_failure():
    user = _FakeUser()
    session = MagicMock()
    session.commit.side_effect = RuntimeError("database locked")

    response, session = _call(
        {"preferences": {"discover_hidden": False}}, user, session)

    assert _status(response) == 500
    session.rollback.assert_called_once_with()


def test_endpoint_rolls_back_staging_failure_without_committing():
    user = _FakeUser()
    user.set_view_property = MagicMock(side_effect=RuntimeError("bad store"))

    response, session = _call(
        {"preferences": {"discover_hidden": False}}, user)

    assert _status(response) == 400
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_named_preference_setter_requires_a_real_store():
    from cps.user_preferences import set_named_preferences

    with pytest.raises(AttributeError, match="store is unavailable"):
        set_named_preferences(object(), {"discover_hidden": True})


def test_endpoint_rejects_guest_without_writing():
    user = _FakeUser(anonymous=True)
    response, session = _call(
        {"preferences": {"discover_hidden": True}}, user)

    assert _status(response) == 401
    session.commit.assert_not_called()


def test_user_store_can_stage_into_null_json_without_early_commit():
    from cps import ub

    user = ub.User()
    user.view_settings = None
    staged_session = MagicMock()
    with patch.object(ub, "session", staged_session):
        user.set_view_property(
            "preferences", "discover_hidden", True, commit=False)

    assert user.view_settings == {"preferences": {"discover_hidden": True}}
    staged_session.commit.assert_not_called()


def test_anonymous_store_accepts_the_uniform_non_committing_signature():
    from cps import ub

    app = flask.Flask(__name__)
    app.secret_key = "test"
    guest = object.__new__(ub.Anonymous)
    with app.test_request_context("/"):
        guest.set_view_property(
            "preferences", "discover_hidden", True, commit=False)
        assert flask.session["view"] == {
            "preferences": {"discover_hidden": True},
        }


def test_frontend_uses_generic_named_preference_hook_for_catalog_preferences():
    hook = _FRONTEND / "lib" / "useNamedPreference.ts"
    card_hook = _FRONTEND / "lib" / "useCardActionsHidden.ts"
    assert hook.is_file()
    hook_src = hook.read_text(encoding="utf-8")
    state_src = (_FRONTEND / "lib" / "namedPreferenceState.ts").read_text(
        encoding="utf-8")
    card_hook_src = card_hook.read_text(encoding="utf-8")
    catalog_src = (_FRONTEND / "pages" / "Catalog.tsx").read_text(encoding="utf-8")
    queries_src = (_FRONTEND / "lib" / "queries.ts").read_text(encoding="utf-8")

    assert "useNamedPreference" in catalog_src
    for token in (
        "discover_hidden", "cwng_discover_hidden_v1",
        "show_hidden_books", "cwng_show_hidden_books_v1",
    ):
        assert token in catalog_src
    assert "useNamedPreference" in card_hook_src
    assert "card_actions_hidden" in card_hook_src
    assert "CARD_ACTIONS_HIDDEN_KEY" in card_hook_src
    assert "/account/preferences" in queries_src
    assert "role?.anonymous" in state_src
    assert "localStorage" in hook_src
