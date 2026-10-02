# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioural checks for the deployment-owned Generic OIDC configuration."""

import logging
import os

import pytest
from flask import Flask

from cps import admin, constants, oauth_config


def complete_env(**overrides):
    env = {
        "GENERIC_OAUTH_ENABLED": "true",
        "GENERIC_OAUTH_CLIENT_ID": "sample-client",
        "GENERIC_OAUTH_CLIENT_SECRET": "sentinel-client-secret",
        "GENERIC_OAUTH_SERVER_URL": "http://127.0.0.1:9900/issuer",
        "GENERIC_OAUTH_AUTH_URL": "http://127.0.0.1:9900/issuer/authorize",
        "GENERIC_OAUTH_TOKEN_URL": "http://127.0.0.1:9900/issuer/token",
        "GENERIC_OAUTH_USERINFO_URL": "http://127.0.0.1:9900/issuer/userinfo",
    }
    env.update(overrides)
    return env


def test_database_remains_the_source_when_no_environment_declaration_exists():
    result = oauth_config.resolve_environment_provider({})

    assert result == {"managed": False, "active": False, "settings": None, "error": None}


def test_environment_false_claims_and_disables_generic_without_partial_database_fallback():
    result = oauth_config.resolve_environment_provider({
        "GENERIC_OAUTH_ENABLED": "false",
        "GENERIC_OAUTH_CLIENT_ID": "old-client",
        "GENERIC_OAUTH_CLIENT_SECRET": "old-secret",
        "GENERIC_OAUTH_SERVER_URL": "http://stale.example/issuer",
    })

    assert result["managed"] is True
    assert result["active"] is False
    assert result["settings"] is None


def test_unknown_environment_name_claims_provider_and_disables_it():
    result = oauth_config.resolve_environment_provider(complete_env(
        GENERIC_OAUTH_CLIENT_SECRETT="typo-secret",
    ))

    assert result["managed"] is True
    assert result["active"] is False
    assert result["settings"] is None
    assert "unknown" in result["error"]


def test_complete_manual_configuration_normalizes_scope_and_applies_group_role_policy():
    result = oauth_config.resolve_environment_provider(complete_env(
        GENERIC_OAUTH_SCOPE="profile  email openid",
        GENERIC_OAUTH_GROUP_CLAIM="roles",
        GENERIC_OAUTH_ALLOWED_GROUPS="readers, operators",
        GENERIC_OAUTH_REQUIRE_GROUP="true",
        GENERIC_OAUTH_ADMIN_GROUP="oidc-admins",
        GENERIC_OAUTH_DEFAULT_ROLE="download,viewer",
    ))

    assert result["active"] is True
    settings = result["settings"]
    assert settings["scope"] == "email openid profile"
    assert settings["oauth_group_claim"] == "roles"
    assert settings["oauth_allowed_groups"] == "readers, operators"
    assert settings["oauth_require_group"] is True
    assert settings["oauth_admin_group"] == "oidc-admins"
    assert settings["oauth_default_role"] == constants.ROLE_DOWNLOAD | constants.ROLE_VIEWER
    assert not settings["oauth_default_role"] & constants.ROLE_ADMIN


def test_metadata_discovery_requires_complete_provider_endpoints():
    metadata = {
        "issuer": "http://127.0.0.1:9900/issuer",
        "authorization_endpoint": "http://127.0.0.1:9900/authorize",
        "token_endpoint": "http://127.0.0.1:9900/token",
        "userinfo_endpoint": "http://127.0.0.1:9900/userinfo",
    }
    result = oauth_config.resolve_environment_provider(
        complete_env(GENERIC_OAUTH_METADATA_URL="http://127.0.0.1:9900/.well-known/openid-configuration"),
        metadata_loader=lambda _url: metadata,
    )

    assert result["active"] is True
    assert result["settings"]["oauth_base_url"] == metadata["issuer"]
    assert result["settings"]["oauth_authorize_url"] == metadata["authorization_endpoint"]

    incomplete = dict(metadata)
    incomplete.pop("userinfo_endpoint")
    invalid = oauth_config.resolve_environment_provider(
        complete_env(GENERIC_OAUTH_METADATA_URL="http://127.0.0.1:9900/.well-known/openid-configuration"),
        metadata_loader=lambda _url: incomplete,
    )
    assert invalid["managed"] is True
    assert invalid["active"] is False
    assert "userinfo" in invalid["error"]


@pytest.mark.parametrize("overrides", [
    {"GENERIC_OAUTH_CLIENT_ID": ""},
    {"GENERIC_OAUTH_CLIENT_SECRET": ""},
    {"GENERIC_OAUTH_USERINFO_URL": ""},
    {"GENERIC_OAUTH_REQUIRE_GROUP": "sometimes"},
    {"GENERIC_OAUTH_DEFAULT_ROLE": "admin"},
])
def test_invalid_or_incomplete_declaration_fails_closed(overrides):
    result = oauth_config.resolve_environment_provider(complete_env(**overrides))

    assert result["managed"] is True
    assert result["active"] is False
    assert result["settings"] is None


def test_environment_secret_is_never_in_error_or_warning(caplog):
    secret = "sentinel-never-log-this-secret"
    with caplog.at_level(logging.WARNING):
        result = oauth_config.resolve_environment_provider({
            "GENERIC_OAUTH_ENABLED": "true",
            "GENERIC_OAUTH_CLIENT_ID": "client",
            "GENERIC_OAUTH_CLIENT_SECRET": secret,
        })

    assert result["active"] is False
    assert secret not in result["error"]
    assert secret not in caplog.text


def test_secret_file_is_read_when_configuration_is_resolved(tmp_path):
    secret_file = tmp_path / "oidc-secret"
    secret_file.write_text("first-secret\n")
    env = complete_env(GENERIC_OAUTH_CLIENT_SECRET="", GENERIC_OAUTH_CLIENT_SECRET_FILE=str(secret_file))

    first = oauth_config.resolve_environment_provider(env)
    secret_file.write_text("rotated-secret\n")
    second = oauth_config.resolve_environment_provider(env)

    assert first["settings"]["oauth_client_secret"] == "first-secret"
    assert second["settings"]["oauth_client_secret"] == "rotated-secret"


def test_missing_secret_file_disables_provider_without_a_database_fallback(tmp_path):
    env = complete_env(
        GENERIC_OAUTH_CLIENT_SECRET="",
        GENERIC_OAUTH_CLIENT_SECRET_FILE=str(tmp_path / "not-mounted"),
    )

    result = oauth_config.resolve_environment_provider(env)

    assert result["managed"] is True
    assert result["active"] is False
    assert result["settings"] is None


def test_valid_environment_provider_selects_oauth_in_memory_without_marking_config_dirty(monkeypatch):
    monkeypatch.setenv("GENERIC_OAUTH_ENABLED", "true")
    monkeypatch.setenv("GENERIC_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GENERIC_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GENERIC_OAUTH_SERVER_URL", "http://127.0.0.1:9900")
    monkeypatch.setenv("GENERIC_OAUTH_AUTH_URL", "http://127.0.0.1:9900/authorize")
    monkeypatch.setenv("GENERIC_OAUTH_TOKEN_URL", "http://127.0.0.1:9900/token")
    monkeypatch.setenv("GENERIC_OAUTH_USERINFO_URL", "http://127.0.0.1:9900/userinfo")
    for name in list(os.environ):
        if name.startswith("GENERIC_OAUTH_") and name not in {
            "GENERIC_OAUTH_ENABLED", "GENERIC_OAUTH_CLIENT_ID", "GENERIC_OAUTH_CLIENT_SECRET",
            "GENERIC_OAUTH_SERVER_URL", "GENERIC_OAUTH_AUTH_URL", "GENERIC_OAUTH_TOKEN_URL",
            "GENERIC_OAUTH_USERINFO_URL",
        }:
            monkeypatch.delenv(name, raising=False)
    runtime_config = type("RuntimeConfig", (), {"config_login_type": 0, "dirty": []})()
    application = Flask(__name__)

    resolved = oauth_config.prepare_application(application, runtime_config)

    assert resolved["active"] is True
    assert runtime_config.config_login_type == constants.LOGIN_OAUTH
    assert runtime_config.dirty == []


def test_explicit_environment_disable_does_not_change_the_saved_login_mode(monkeypatch):
    monkeypatch.setenv("GENERIC_OAUTH_ENABLED", "false")
    for name in list(os.environ):
        if name.startswith("GENERIC_OAUTH_") and name != "GENERIC_OAUTH_ENABLED":
            monkeypatch.delenv(name, raising=False)
    runtime_config = type("RuntimeConfig", (), {"config_login_type": 0, "dirty": []})()
    application = Flask(__name__)

    resolved = oauth_config.prepare_application(application, runtime_config)

    assert resolved["managed"] is True
    assert resolved["active"] is False
    assert runtime_config.config_login_type == 0
    assert runtime_config.dirty == []


def test_runtime_oauth_selection_survives_unrelated_config_save_and_reload(
    monkeypatch, tmp_path
):
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import config_sql

    for name in list(os.environ):
        if name.startswith("GENERIC_OAUTH_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in complete_env().items():
        monkeypatch.setenv(name, value)

    engine = create_engine("sqlite:///{}".format(tmp_path / "runtime-oauth.db"))
    config_sql._Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    key = Fernet.generate_key()
    config_sql.load_configuration(session, key)
    stored = session.query(config_sql._Settings).one()
    stored.config_login_type = constants.LOGIN_STANDARD
    session.commit()

    runtime_config = config_sql.ConfigSQL()
    runtime_config.init_config(session, key, None)
    resolved = oauth_config.prepare_application(Flask(__name__), runtime_config)
    assert resolved["active"] is True
    assert runtime_config.config_login_type == constants.LOGIN_OAUTH

    runtime_config.config_calibre_web_title = "Unrelated setting saved"
    runtime_config.save()

    assert runtime_config.config_login_type == constants.LOGIN_OAUTH
    assert runtime_config.dirty == []
    assert session.query(config_sql._Settings).one().config_login_type == constants.LOGIN_STANDARD

    session.close()
    engine.dispose()


def test_forged_admin_post_cannot_persist_environment_owned_provider_fields(monkeypatch):
    provider = {
        "provider_name": "generic",
        "id": 99,
        "environment_managed": True,
        "oauth_client_id": "runtime-client",
        "oauth_client_secret": "runtime-secret",
        "oauth_base_url": "http://127.0.0.1:9900/issuer",
    }
    monkeypatch.setattr(admin.oauth_bb, "get_oauth_blueprints", lambda: [provider])
    class NoWriteSession:
        @staticmethod
        def query(*_args, **_kwargs):
            pytest.fail("environment-owned OIDC fields must not write an OAuthProvider row")

    monkeypatch.setattr(admin.ub, "session", NoWriteSession())
    forged_form = {
        "config_generic_oauth_client_id": "attacker-client",
        "config_generic_oauth_client_secret": "attacker-secret",
        "config_generic_oauth_server_url": "http://attacker.invalid/issuer",
        "config_generic_oauth_require_group": "on",
        "config_generic_oauth_default_admin_role": "on",
    }

    changed, message = admin._configuration_oauth_helper(forged_form)

    assert changed is False
    assert message is None


def test_incomplete_environment_descriptor_does_not_fall_back_to_stale_active_database_provider(
    monkeypatch,
):
    from types import SimpleNamespace
    from cps import oauth_bb, ub

    providers = [
        SimpleNamespace(
            id=1, provider_name="github", active=False,
            oauth_client_id="", oauth_client_secret="",
        ),
        SimpleNamespace(
            id=2, provider_name="google", active=False,
            oauth_client_id="", oauth_client_secret="",
        ),
        SimpleNamespace(
            id=3,
            provider_name="generic",
            active=True,
            scope="stale-scope",
            oauth_client_id="stale-client",
            oauth_client_secret="stale-secret",
            oauth_base_url="https://stale.example/issuer",
            oauth_authorize_url="https://stale.example/authorize",
            oauth_token_url="https://stale.example/token",
            oauth_userinfo_url="https://stale.example/userinfo",
            metadata_url="https://stale.example/.well-known/openid-configuration",
            username_mapper="stale-user",
            email_mapper="stale-email",
            login_button="Stale button",
            oauth_admin_group="stale-admins",
            oauth_group_claim="stale-groups",
            oauth_allowed_groups="stale-readers",
            oauth_require_group=True,
            oauth_default_role=constants.ROLE_ADMIN,
        ),
    ]

    class Query:
        def count(self):
            return len(providers)

        def filter(self, _predicate):
            return self

        def all(self):
            return [provider for provider in providers if provider.provider_name in {"github", "google"}]

        def filter_by(self, **filters):
            return SimpleNamespace(first=lambda: next(
                (provider for provider in providers
                 if all(getattr(provider, name) == value for name, value in filters.items())),
                None,
            ))

    class Session:
        @staticmethod
        def query(_model):
            return Query()

    monkeypatch.setattr(ub, "session", Session())
    monkeypatch.setattr(oauth_bb, "OAuthBackend", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(oauth_bb, "oauth_check", {})
    monkeypatch.setattr(oauth_bb, "register_oauth_blueprint", lambda *_args: None)
    monkeypatch.setattr(oauth_bb.config, "config_default_role", 0, raising=False)

    application = Flask("stale-oauth-provider-test")
    application.extensions[oauth_config.ENVIRONMENT_EXTENSION] = {
        "managed": True,
        "active": False,
        "settings": None,
        "error": "client id and client secret are required",
    }
    generated = oauth_bb.generate_oauth_blueprints(application)
    generic = next(provider for provider in generated if provider["provider_name"] == "generic")

    assert generic["environment_managed"] is True
    assert generic["active"] is False
    assert generic["oauth_client_id"] == ""
    assert generic["oauth_client_secret"] == ""
    assert generic["oauth_base_url"] == ""
    assert generic["oauth_authorize_url"] == ""
    assert generic["oauth_token_url"] == ""
    assert generic["oauth_userinfo_url"] == ""
    assert generic["metadata_url"] == ""
    assert generic["oauth_group_claim"] == "groups"
    assert generic["oauth_allowed_groups"] == ""
    assert generic["oauth_default_role"] is None
    assert providers[2].oauth_client_secret == "stale-secret"
