# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the bare-metal follow-up to PR #1882.

The application must stay available when its profile-picture file cannot be
created during startup, and the classic profile-picture consumers must recover
when that file is absent or unusable.  Upload staging must likewise tolerate a
non-numeric LinuxServer-style PUID/PGID instead of abandoning ownership setup.
"""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest

import cps
from cps import constants, cwa_functions, editbooks


DATA_URI = "data:image/png;base64,iVBORw0KGgo="


def _profile_app():
    app = flask.Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(cwa_functions.profile_pictures)
    return app


@pytest.mark.unit
def test_profiles_startup_creation_keeps_original_bytes(tmp_path, monkeypatch):
    profiles_path = tmp_path / "user_profiles.json"
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))

    cps._ensure_user_profiles_json()

    assert profiles_path.read_bytes() == b"{\n}"


@pytest.mark.unit
def test_profiles_startup_creation_failure_warns_and_continues(tmp_path, monkeypatch):
    profiles_path = tmp_path / "missing-config" / "user_profiles.json"
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))

    with patch.object(cps.log, "warning") as warning:
        cps._ensure_user_profiles_json()

    warning.assert_called_once()
    assert str(profiles_path) in warning.call_args.args


@pytest.mark.unit
def test_profiles_startup_keeps_existing_file_byte_identical(tmp_path, monkeypatch):
    profiles_path = tmp_path / "user_profiles.json"
    original = b'{"alice":"unchanged"}\n'
    profiles_path.write_bytes(original)
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))

    cps._ensure_user_profiles_json()

    assert profiles_path.read_bytes() == original


@pytest.mark.unit
def test_create_app_survives_absent_profiles_directory(tmp_path, monkeypatch):
    """Exercise the real factory so any unguarded profile write fails startup."""
    from cps import calibre_init, cw_babel, schedule, services

    profiles_path = tmp_path / "absent-config" / "user_profiles.json"
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))
    startup_app = flask.Flask("test_1882_startup")
    monkeypatch.setattr(cps, "app", startup_app)
    monkeypatch.setattr(cps, "_process_runtime_state", cps._ProcessRuntimeState())

    monkeypatch.setattr(cps, "csrf", None)
    monkeypatch.setattr(cps.cli_param, "init", lambda: None)
    monkeypatch.setattr(cps.cli_param, "settings_path", str(tmp_path / "app.db"))
    monkeypatch.setattr(cps.cli_param, "user_credentials", None)
    monkeypatch.setattr(cps.cli_param, "memory_backend", False)
    monkeypatch.setattr(cps.cli_param, "dry_run", False)
    monkeypatch.setattr(cps.ub, "init_db", lambda _path: None)
    monkeypatch.setattr(cps.ub, "session", SimpleNamespace(bind=None))
    monkeypatch.setattr(cps.ub, "password_change", lambda _credentials: None)
    monkeypatch.setattr(cps.ub, "backfill_annotation_content_ids", lambda *_args: None)
    monkeypatch.setattr(cps.ub, "oauth_support", False)
    monkeypatch.setattr(cps.config_sql, "get_encryption_key", lambda _path: (None, None))
    monkeypatch.setattr(cps.config_sql, "load_configuration", lambda *_args: None)
    monkeypatch.setattr(cps.config_sql, "get_flask_session_key", lambda _session: "test")
    monkeypatch.setattr(cps.config, "init_config", lambda *_args: None)
    monkeypatch.setattr(cps.config, "config_oauth_redirect_host", "", raising=False)
    monkeypatch.setattr(cps.config, "config_session", 0, raising=False)
    monkeypatch.setattr(cps.config, "config_ratelimiter", False, raising=False)
    monkeypatch.setattr(cps.config, "config_limiter_uri", "", raising=False)
    monkeypatch.setattr(cps.config, "config_limiter_options", "", raising=False)
    monkeypatch.setattr(cps.config, "schedule_reconnect", False, raising=False)
    monkeypatch.setattr(cps.config, "store_calibre_uuid", lambda *_args: None)
    monkeypatch.setattr(cps, "apply_https_runtime_config", lambda: None)
    monkeypatch.setattr(calibre_init, "init_calibre_db_from_config", lambda *_args: None)
    monkeypatch.setattr(cps.calibre_db, "init_db", lambda: None)
    monkeypatch.setattr(cps.updater_thread, "init_updater", lambda *_args: None)
    monkeypatch.setattr(cps.updater_thread, "start", lambda: None)
    monkeypatch.setattr(cps, "ReverseProxied", lambda wsgi_app: wsgi_app)
    monkeypatch.setattr(cps, "Principal", lambda _app: None)
    monkeypatch.setattr(cps.lm, "init_app", lambda _app: None)
    monkeypatch.setattr(cps.web_server, "init_app", lambda *_args: None)
    monkeypatch.setattr(cw_babel.babel, "init_app", lambda *_args, **_kwargs: None)
    if hasattr(cw_babel.babel, "localeselector"):
        monkeypatch.setattr(cw_babel.babel, "localeselector", lambda _selector: None)
    monkeypatch.setattr(services, "ldap", None)
    monkeypatch.setattr(services, "goodreads_support", None)
    monkeypatch.setattr(cps.limiter, "init_app", lambda _app: None)
    monkeypatch.setattr(schedule, "register_scheduled_tasks", lambda _enabled: None)
    monkeypatch.setattr(schedule, "register_startup_tasks", lambda: None)

    with patch.object(cps.log, "warning") as warning:
        result = cps.create_app()

    assert result is startup_app
    assert not profiles_path.exists()
    warning.assert_called_once()
    assert str(profiles_path) in warning.call_args.args


@pytest.mark.unit
@pytest.mark.parametrize("contents", [None, "", "{not-json", "[]"])
def test_profiles_reader_returns_empty_object_for_unusable_file(
    tmp_path, monkeypatch, contents
):
    profiles_path = tmp_path / "user_profiles.json"
    if contents is not None:
        profiles_path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))
    app = _profile_app()

    with app.app_context(), patch.object(cwa_functions.log, "warning") as warning:
        result = inspect.unwrap(cwa_functions.user_profiles_json)()
        response = app.make_response(result)

    assert response.status_code == 200
    assert response.get_json() == {}
    warning.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("contents", [None, "", "{not-json", "[]"])
def test_profiles_writer_repairs_unusable_file(tmp_path, monkeypatch, contents):
    profiles_path = tmp_path / "user_profiles.json"
    if contents is not None:
        profiles_path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))
    app = _profile_app()
    admin = SimpleNamespace(role_admin=lambda: True, name="admin")

    with app.test_request_context(
        "/me/profile-picture",
        method="POST",
        data={"username": "alice", "image_data": DATA_URI},
    ), patch.object(cwa_functions, "current_user", admin):
        response = inspect.unwrap(cwa_functions.set_profile_picture)()

    assert response.status_code == 302
    assert json.loads(profiles_path.read_text(encoding="utf-8")) == {
        "alice": DATA_URI,
    }


@pytest.mark.unit
def test_profiles_writer_keeps_existing_output_format(tmp_path, monkeypatch):
    profiles_path = tmp_path / "user_profiles.json"
    profiles_path.write_text('{"bob": "data:image/png;base64,Ym9i"}', encoding="utf-8")
    monkeypatch.setattr(constants, "USER_PROFILES_JSON", str(profiles_path))
    app = _profile_app()
    admin = SimpleNamespace(role_admin=lambda: True, name="admin")

    with app.test_request_context(
        "/me/profile-picture",
        method="POST",
        data={"username": "alice", "image_data": DATA_URI},
    ), patch.object(cwa_functions, "current_user", admin):
        inspect.unwrap(cwa_functions.set_profile_picture)()

    assert profiles_path.read_bytes() == (
        b'{\n'
        b'    "bob": "data:image/png;base64,Ym9i",\n'
        b'    "alice": "data:image/png;base64,iVBORw0KGgo="\n'
        b'}'
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bad_variable", "expected_uid", "expected_gid"),
    [("PUID", 1000, 2002), ("PGID", 2001, 1000)],
)
def test_bad_ingest_owner_id_warns_and_falls_back(
    tmp_path, monkeypatch, bad_variable, expected_uid, expected_gid
):
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))
    monkeypatch.setenv("NETWORK_SHARE_MODE", "false")
    monkeypatch.setenv("PUID", "2001")
    monkeypatch.setenv("PGID", "2002")
    monkeypatch.setenv(bad_variable, "not-a-number")
    uploaded = SimpleNamespace(filename="book.epub")

    with patch.object(editbooks.os, "chown") as chown, \
            patch.object(editbooks.log, "warning") as warning:
        final_path = editbooks._get_ingest_path(uploaded, prefix_parts=["new", 1])

    chown.assert_called_once_with(str(tmp_path), expected_uid, expected_gid)
    assert final_path.endswith("_book.epub")
    assert any(bad_variable in str(call) for call in warning.call_args_list)


@pytest.mark.unit
def test_missing_ingest_owner_ids_silently_keep_default(tmp_path, monkeypatch):
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))
    monkeypatch.setenv("NETWORK_SHARE_MODE", "false")
    monkeypatch.delenv("PUID", raising=False)
    monkeypatch.delenv("PGID", raising=False)
    uploaded = SimpleNamespace(filename="book.epub")

    with patch.object(editbooks.os, "chown") as chown, \
            patch.object(editbooks.log, "warning") as warning:
        editbooks._get_ingest_path(uploaded)

    chown.assert_called_once_with(str(tmp_path), 1000, 1000)
    warning.assert_not_called()


@pytest.mark.unit
def test_valid_ingest_owner_ids_are_used_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))
    monkeypatch.setenv("NETWORK_SHARE_MODE", "false")
    monkeypatch.setenv("PUID", "2001")
    monkeypatch.setenv("PGID", "2002")
    uploaded = SimpleNamespace(filename="book.epub")

    with patch.object(editbooks.os, "chown") as chown, \
            patch.object(editbooks.log, "warning") as warning:
        editbooks._get_ingest_path(uploaded)

    chown.assert_called_once_with(str(tmp_path), 2001, 2002)
    warning.assert_not_called()
