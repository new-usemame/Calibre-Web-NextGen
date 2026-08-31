# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo pairing API: token compatibility, auth boundaries and HTTP contract."""

import inspect
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from flask_wtf.csrf import CSRFProtect


def _user(*, user_id=1, authenticated=True, anonymous=False, admin=False):
    return SimpleNamespace(
        id=user_id,
        is_authenticated=authenticated,
        is_anonymous=anonymous,
        role_admin=lambda: admin,
    )


def _ub(*, target_exists=True):
    value = SimpleNamespace(id=1) if target_exists else None
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = value
    return SimpleNamespace(
        session=session,
        session_commit=MagicMock(return_value=True),
        User=MagicMock(),
    )


def _ctx(path, method="GET", host="books.example.test"):
    app = flask.Flask(__name__)
    app.config["SERVER_NAME"] = host
    app.add_url_rule(
        "/kobo/<auth_token>",
        endpoint="kobo.TopLevelEndpoint",
        view_func=lambda auth_token: auth_token,
    )
    return app.test_request_context(path, method=method, base_url=f"https://{host}")


def _json(response):
    raw = response[0] if isinstance(response, tuple) else response
    return json.loads(raw.get_data())


@pytest.mark.unit
def test_anonymous_browse_user_cannot_view_or_create_token():
    from cps.api import kobo_pairing as mod
    for method, view in (("GET", mod.get_kobo_sync_token), ("POST", mod.create_kobo_sync_token)):
        with _ctx("/api/v1/account/kobo-sync-token", method=method), \
                patch.object(mod, "current_user", _user(authenticated=False, anonymous=True)):
            response = inspect.unwrap(view)()
        assert response[1] == 401
        assert _json(response)["error"]["code"] == "unauthorized"


@pytest.mark.unit
@pytest.mark.parametrize("view_name,method", [
    ("get_kobo_sync_token", "GET"),
    ("create_kobo_sync_token", "POST"),
    ("delete_kobo_sync_token", "DELETE"),
])
def test_non_admin_cannot_manage_another_users_token(view_name, method):
    from cps.api import kobo_pairing as mod
    view = getattr(mod, view_name)
    with _ctx("/api/v1/admin/users/2/kobo-sync-token", method=method), \
            patch.object(mod, "current_user", _user(user_id=1)), \
            patch.object(mod, "ub", _ub()):
        response = inspect.unwrap(view)(2)
    assert response[1] == 403
    assert _json(response)["error"]["code"] == "forbidden"


@pytest.mark.unit
def test_missing_admin_target_is_404_without_minting():
    from cps.api import kobo_pairing as mod
    create = MagicMock()
    with _ctx("/api/v1/admin/users/404/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user(admin=True)), \
            patch.object(mod, "ub", _ub(target_exists=False)), \
            patch.object(mod, "create_or_view_auth_token", create):
        response = inspect.unwrap(mod.create_kobo_sync_token)(404)
    assert response[1] == 404
    create.assert_not_called()


@pytest.mark.unit
def test_disabled_kobo_sync_refuses_token_creation():
    from cps.api import kobo_pairing as mod
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=False)):
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == 409
    assert _json(response)["error"]["code"] == "kobo_sync_disabled"


@pytest.mark.unit
def test_disabled_kobo_sync_still_exposes_an_existing_token():
    from cps.api import kobo_pairing as mod
    row = SimpleNamespace(auth_token="d" * 32)
    with _ctx("/api/v1/account/kobo-sync-token"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=False)), \
            patch.object(mod, "find_auth_token", return_value=row) as find:
        response = inspect.unwrap(mod.get_kobo_sync_token)()
    status = response[1] if isinstance(response, tuple) else response.status_code
    assert status == 200
    assert _json(response)["sync_url"] == f"https://books.example.test/kobo/{'d' * 32}"
    find.assert_called_once_with(1)


@pytest.mark.unit
def test_disabled_kobo_sync_still_allows_revocation():
    from cps.api import kobo_pairing as mod
    with _ctx("/api/v1/account/kobo-sync-token", method="DELETE"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=False)), \
            patch.object(mod, "revoke_auth_token", return_value=True) as revoke:
        response = inspect.unwrap(mod.delete_kobo_sync_token)()
    assert response[1] == 204
    revoke.assert_called_once_with(1)


@pytest.mark.unit
def test_get_views_existing_state_without_creating():
    from cps.api import kobo_pairing as mod
    find = MagicMock(return_value=None)
    create = MagicMock()
    with _ctx("/api/v1/account/kobo-sync-token"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "find_auth_token", find), \
            patch.object(mod, "create_or_view_auth_token", create):
        response = inspect.unwrap(mod.get_kobo_sync_token)()
    assert _json(response) == {
        "configured": False,
        "is_localhost": False,
        "server_url": "https://books.example.test",
        "sync_url": None,
        "user_id": 1,
    }
    find.assert_called_once_with(1)
    create.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("created,status", [(True, 201), (False, 200)])
def test_create_or_view_returns_tokenized_kobo_url(created, status):
    from cps.api import kobo_pairing as mod
    row = SimpleNamespace(auth_token="a" * 32)
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, created, True)) as create:
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == status
    body = _json(response)
    assert body["configured"] is True
    assert body["sync_url"] == f"https://books.example.test/kobo/{'a' * 32}"
    assert body["server_url"] == "https://books.example.test"
    assert response[0].headers["Cache-Control"] == "private, no-store"
    create.assert_called_once_with(1)


@pytest.mark.unit
def test_admin_can_create_for_another_user_only_on_admin_route():
    from cps.api import kobo_pairing as mod
    row = SimpleNamespace(auth_token="b" * 32)
    with _ctx("/api/v1/admin/users/7/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user(user_id=1, admin=True)), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, True, True)) as create:
        response = inspect.unwrap(mod.create_kobo_sync_token)(7)
    assert response[1] == 201
    assert _json(response)["user_id"] == 7
    create.assert_called_once_with(7)


@pytest.mark.unit
def test_create_commit_failure_returns_500_without_putting_token_in_error():
    from cps.api import kobo_pairing as mod
    secret = "c" * 32
    row = SimpleNamespace(auth_token=secret)
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, True, False)):
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == 500
    assert secret not in response[0].get_data(as_text=True)


@pytest.mark.unit
@pytest.mark.parametrize("committed,status", [(True, 204), (False, 500)])
def test_delete_reports_whether_revocation_commit_landed(committed, status):
    from cps.api import kobo_pairing as mod
    fake_ub = _ub()
    fake_ub.session_commit.return_value = committed
    with _ctx("/api/v1/account/kobo-sync-token", method="DELETE"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", fake_ub), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "revoke_auth_token") as revoke:
        response = inspect.unwrap(mod.delete_kobo_sync_token)()
    assert response[1] == status
    revoke.assert_called_once_with(1)
    fake_ub.session_commit.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "[::1]:8083"])
def test_localhost_detection_uses_the_parsed_hostname(host):
    from cps.api import kobo_pairing as mod
    with _ctx("/api/v1/account/kobo-sync-token", host=host):
        assert mod._is_localhost() is True


@pytest.mark.unit
def test_http_contract_exposes_read_create_and_delete_methods_separately():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.register_blueprint(api_v1)
    methods = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.endswith("/kobo-sync-token"):
            methods.setdefault(rule.rule, set()).update(rule.methods)
    assert methods["/api/v1/account/kobo-sync-token"] >= {"GET", "POST", "DELETE"}
    assert methods["/api/v1/admin/users/<int:user_id>/kobo-sync-token"] >= {
        "GET", "POST", "DELETE",
    }


@pytest.mark.unit
def test_tokenless_post_is_rejected_by_real_csrf_middleware():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.config.update(
        SECRET_KEY="unit-test-only",
        WTF_CSRF_ENABLED=True,
    )
    CSRFProtect(app)
    app.register_blueprint(api_v1)
    with patch("cps.api.current_user", _user()), \
            patch("cps.api.config", SimpleNamespace(
                config_allow_reverse_proxy_header_login=False,
                config_anonbrowse=0,
            )):
        response = app.test_client().post("/api/v1/account/kobo-sync-token")
    assert response.status_code == 400


@pytest.mark.unit
def test_device_json_serializes_naive_database_timestamps_as_utc():
    from cps.annotations import _device_json
    observed = datetime(2026, 8, 30, 14, 15, 16)
    device = SimpleNamespace(
        public_id="device-1",
        display_name="Kitchen Kobo",
        kind="kobo",
        model="Libra",
        firmware_version="4.43",
        first_seen_at=observed,
        last_seen_at=observed,
        active=True,
    )
    inventory = SimpleNamespace(item_count=2, observed_at=observed)
    storage = SimpleNamespace(free_bytes=1, total_bytes=2, observed_at=observed)

    payload = _device_json(
        device,
        inventory_report=inventory,
        storage_snapshot=storage,
        last_position_at=observed,
    )

    for field in (
        "first_seen", "last_seen", "inventory_observed",
        "storage_observed", "last_position_at",
    ):
        assert payload[field] == "2026-08-30T14:15:16+00:00"


@pytest.mark.unit
def test_shared_helper_preserves_classic_token_semantics():
    from cps import kobo_auth as mod

    class _RemoteAuthToken:
        # SQLAlchemy models expose these on the class for query expressions.
        user_id = MagicMock()
        token_type = MagicMock()

    created = _RemoteAuthToken()
    fake_ub = SimpleNamespace(
        session=MagicMock(),
        RemoteAuthToken=MagicMock(return_value=created),
        session_commit=MagicMock(return_value=True),
    )
    fake_ub.session.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    with patch.object(mod, "ub", fake_ub), patch.object(mod, "urandom", return_value=b"\x0f" * 16):
        row, was_created, committed = mod.create_or_view_auth_token(9)
    assert row is created
    assert was_created is True and committed is True
    assert created.user_id == 9
    assert created.expiration == datetime.max
    assert created.auth_token == "0f" * 16
    assert created.token_type == 1
    fake_ub.session.add.assert_called_once_with(created)
