# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Blueprint, Flask
from flask_babel import Babel
from flask_dance.consumer import oauth_authorized
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix

import cps
from cps.reverseproxy import ReverseProxied


def _stub_real_bootstrap(monkeypatch):
    """Leave Flask construction real while isolating process and storage work."""
    from cps import calibre_init, cw_babel, helper, schedule, services as real_services

    monkeypatch.setattr(cps, "app", cps.app)
    monkeypatch.setattr(cps, "_process_runtime_state", cps._ProcessRuntimeState())
    monkeypatch.setattr(real_services, "goodreads_support", None)
    monkeypatch.setattr(cps.cli_param, "init", lambda: None)
    monkeypatch.setattr(cps.cli_param, "settings_path", "/tmp/factory-app.db")
    monkeypatch.setattr(cps.cli_param, "user_credentials", None)
    monkeypatch.setattr(cps.cli_param, "memory_backend", False)
    monkeypatch.setattr(cps.cli_param, "dry_run", False)
    monkeypatch.setattr(cps.ub, "init_db", lambda _path: None)
    monkeypatch.setattr(cps.ub, "session", SimpleNamespace(bind=None))
    monkeypatch.setattr(cps.ub, "password_change", lambda _credentials: None)
    monkeypatch.setattr(cps.ub, "backfill_annotation_content_ids", lambda *_args: None)
    monkeypatch.setattr(cps.ub, "oauth_support", False)
    monkeypatch.setattr(
        cps.ub,
        "Anonymous",
        type("FactoryAnonymous", (), {"is_authenticated": False, "is_anonymous": True}),
    )
    monkeypatch.setattr(cps.config_sql, "get_encryption_key", lambda _path: (None, None))
    monkeypatch.setattr(cps.config_sql, "load_configuration", lambda *_args: None)
    monkeypatch.setattr(cps.config_sql, "get_flask_session_key", lambda _session: "test")
    monkeypatch.setattr(cps.config, "init_config", lambda *_args: None)
    config_values = {
        "config_login_type": 0,
        "config_use_https": False,
        "config_oauth_redirect_host": "",
        "config_session": 0,
        "config_ratelimiter": False,
        "config_limiter_uri": "",
        "config_limiter_options": "",
        "config_allow_reverse_proxy_header_login": False,
        "config_reverse_proxy_login_header_name": "",
        "config_goodreads_api_key": "",
        "config_use_goodreads": False,
        "config_use_google_drive": False,
        "config_trustedhosts": "",
        "schedule_reconnect": False,
        "store_calibre_uuid": lambda *_args: None,
    }
    for name, value in config_values.items():
        monkeypatch.setattr(cps.config, name, value, raising=False)

    monkeypatch.setattr(cps, "_ensure_user_profiles_json", lambda: None)
    monkeypatch.setattr(calibre_init, "init_calibre_db_from_config", lambda *_args: None)
    monkeypatch.setattr(cps.calibre_db, "init_db", lambda: None)
    monkeypatch.setattr(cps.calibre_db, "ensure_session", lambda: None)
    monkeypatch.setattr(cps.calibre_db, "_desktop_compat", False)
    monkeypatch.setattr(cps.calibre_db, "session", None)
    monkeypatch.setattr(cps.calibre_db, "session_factory", None)
    monkeypatch.setattr(helper, "scavenge_staged_cover_files", lambda: None)
    updater_init = MagicMock()
    monkeypatch.setattr(cps.updater_thread, "init_updater", updater_init)
    updater_start = MagicMock()
    monkeypatch.setattr(cps.updater_thread, "start", updater_start)
    monkeypatch.setattr(cps, "Principal", lambda _app: None)
    monkeypatch.setattr(cps.lm, "_user_callback", lambda *_args: None)
    web_server_init = MagicMock()
    monkeypatch.setattr(cps.web_server, "init_app", web_server_init)
    monkeypatch.setattr(cw_babel.babel, "init_app", lambda *_args, **_kwargs: None)
    if hasattr(cw_babel.babel, "localeselector"):
        monkeypatch.setattr(cw_babel.babel, "localeselector", lambda _selector: None)
    monkeypatch.setattr(cps.limiter, "init_app", lambda _app: None)

    scheduled = MagicMock()
    startup = MagicMock()
    monkeypatch.setattr(schedule, "register_scheduled_tasks", scheduled)
    monkeypatch.setattr(schedule, "register_startup_tasks", startup)
    service_bundle = SimpleNamespace(ldap=None, goodreads_support=None)
    service_bundle.factory_probe = SimpleNamespace(
        updater_init=updater_init,
        web_server_init=web_server_init,
    )
    return service_bundle, updater_start, scheduled, startup


def _factory_config(**overrides):
    values = {
        "init_config": lambda *_args: None,
        "config_login_type": 0,
        "config_use_https": False,
        "config_oauth_redirect_host": "",
        "config_session": 0,
        "config_ratelimiter": False,
        "config_limiter_uri": "",
        "config_limiter_options": "",
        "config_allow_reverse_proxy_header_login": False,
        "config_reverse_proxy_login_header_name": "",
        "config_goodreads_api_key": "",
        "config_use_goodreads": False,
        "config_use_google_drive": False,
        "config_trustedhosts": "",
        "schedule_reconnect": False,
        "store_calibre_uuid": lambda *_args: None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _middleware_names(application):
    names = []
    middleware = application.wsgi_app
    seen = set()
    while id(middleware) not in seen:
        seen.add(id(middleware))
        names.append(type(middleware).__name__)
        if isinstance(middleware, ReverseProxied):
            middleware = middleware.app
        elif isinstance(middleware, ProxyFix):
            middleware = middleware.app
        else:
            break
    return names


def _hook_counts(application):
    return {
        "before": sum(len(items) for items in application.before_request_funcs.values()),
        "after": sum(len(items) for items in application.after_request_funcs.values()),
        "teardown_appcontext": len(application.teardown_appcontext_funcs),
        "teardown_request": len(application.teardown_request_funcs.get(None, [])),
        "error_handlers": sum(
            len(exception_map)
            for code_map in application.error_handler_spec.values()
            for exception_map in code_map.values()
        ),
    }


@pytest.mark.unit
def test_factory_constructs_two_independent_apps_without_process_job_duplication(monkeypatch):
    """A second factory call gets its own app but cannot restart process jobs."""
    from cps import web

    services, updater_start, scheduled, startup = _stub_real_bootstrap(monkeypatch)

    first = cps.create_app(cps.config, services)
    first_counts = _hook_counts(first)
    first_jobs = (updater_start.call_count, scheduled.call_count, startup.call_count)

    second = cps.create_app(cps.config, services)
    second_counts = _hook_counts(second)
    second_jobs = (updater_start.call_count, scheduled.call_count, startup.call_count)

    assert first is not second
    assert first_counts == second_counts
    assert len(first.after_request_funcs[None]) == len(set(first.after_request_funcs[None]))
    assert len(second.after_request_funcs[None]) == len(set(second.after_request_funcs[None]))
    for application in (first, second):
        after_hooks = application.after_request_funcs[None]
        assert after_hooks.count(cps.protect_user_specific_catalog_responses) == 1
        assert after_hooks.count(web.add_security_headers) == 1
        assert after_hooks.count(web.add_static_asset_cache_headers) == 1
    assert _middleware_names(first).count("ProxyFix") == 1
    assert _middleware_names(second).count("ProxyFix") == 1
    assert first_jobs == (1, 1, 1)
    assert second_jobs == first_jobs

    first.add_url_rule("/factory-probe", "first_factory_probe", lambda: "first")
    second.add_url_rule("/factory-probe", "second_factory_probe", lambda: "second")
    assert first.test_client().get("/factory-probe").data == b"first"
    assert second.test_client().get("/factory-probe").data == b"second"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first_setting", "second_setting"),
    [(1, 0), (0, 1)],
)
def test_session_policy_is_local_to_each_app_in_both_construction_orders(
    monkeypatch, first_setting, second_setting
):
    """Constructing app B cannot rewrite app A's login-session policy."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    first = cps.create_app(_factory_config(config_session=first_setting), services)
    first_policy = first.login_manager.session_protection

    second = cps.create_app(_factory_config(config_session=second_setting), services)

    assert first.login_manager is not second.login_manager
    assert first.login_manager.session_protection == first_policy
    assert first_policy == ("strong" if first_setting else "basic")
    assert second.login_manager.session_protection == (
        "strong" if second_setting else "basic"
    )


@pytest.mark.unit
def test_distinct_service_bundles_cannot_repeat_process_startup(monkeypatch):
    """Process jobs run once even when callers supply different service objects."""
    first_services, updater_start, scheduled, startup = _stub_real_bootstrap(monkeypatch)
    second_services = SimpleNamespace(ldap=None, goodreads_support=None)

    first = cps.create_app(cps.config, first_services)
    second = cps.create_app(cps.config, second_services)

    assert first is not second
    assert (updater_start.call_count, scheduled.call_count, startup.call_count) == (
        1,
        1,
        1,
    )


@pytest.mark.unit
def test_three_explicit_apps_keep_app_state_local_and_process_state_fixed(monkeypatch):
    """Three factory products have local login/Babel state and one process runtime."""
    from cps import cw_babel

    services, updater_start, scheduled, startup = _stub_real_bootstrap(monkeypatch)
    monkeypatch.setattr(cw_babel, "babel", Babel())

    applications = [cps.create_app(cps.config, services) for _ in range(3)]

    assert len({id(application) for application in applications}) == 3
    assert len({id(application.login_manager) for application in applications}) == 3
    assert len(
        {id(application.extensions["babel"].instance) for application in applications}
    ) == 3
    assert services.factory_probe.web_server_init.call_count == 1
    assert services.factory_probe.updater_init.call_count == 1
    assert (updater_start.call_count, scheduled.call_count, startup.call_count) == (
        1,
        1,
        1,
    )


@pytest.mark.unit
def test_incompatible_process_limiter_config_fails_before_mutating_first_app(monkeypatch):
    """A second limiter backend cannot silently replace app A's live backend."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    process_limiter = Limiter(
        key_func=lambda: "factory-test",
        headers_enabled=True,
        auto_check=False,
        swallow_errors=False,
    )
    monkeypatch.setattr(cps, "limiter", process_limiter)
    first = cps.create_app(
        _factory_config(config_ratelimiter=True, config_limiter_uri="memory://"),
        services,
    )
    first_storage = process_limiter.storage
    compatible = cps.create_app(
        _factory_config(config_ratelimiter=True, config_limiter_uri="memory://"),
        services,
    )
    assert compatible.extensions["limiter"] == {process_limiter}
    assert process_limiter.storage is first_storage

    with pytest.raises(RuntimeError, match="process-scoped"):
        cps.create_app(
            _factory_config(config_ratelimiter=False, config_limiter_uri="memory://"),
            services,
        )

    assert first.extensions["limiter"] == {process_limiter}
    assert process_limiter.storage is first_storage


@pytest.mark.unit
def test_repeated_no_argument_factory_call_is_idempotent(monkeypatch):
    """The compatibility factory can be called repeatedly without accumulating hooks."""
    _stub_real_bootstrap(monkeypatch)

    first = cps.create_app()
    first_counts = _hook_counts(first)
    second = cps.create_app()
    third = cps.create_app()

    assert first is second is third is cps.app
    assert _hook_counts(second) == first_counts
    assert _hook_counts(third) == first_counts
    assert len(first.after_request_funcs[None]) == len(
        set(first.after_request_funcs[None])
    )


@pytest.mark.unit
def test_explicit_factory_keeps_compatibility_singleton_identity(monkeypatch):
    """Existing and later imports of cps.app must resolve to one stable object."""
    from cps import oauth_bb, web

    held = cps.app
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    fresh = cps.create_app(cps.config, services)

    assert fresh is not held
    assert cps.app is held
    assert oauth_bb.app is held
    assert web.app is held


@pytest.mark.unit
def test_all_no_argument_fallbacks_target_the_stable_singleton(monkeypatch):
    """Factory, error-handler, and OAuth fallbacks all target the same cps.app."""
    from cps import error_handler, oauth_bb

    held = cps.app
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    fresh = cps.create_app(cps.config, services)
    assert fresh is not held

    assert cps.create_app() is held
    error_handler.init_errorhandler()
    assert _hook_counts(held)["error_handlers"] > 0

    generated_targets = []
    monkeypatch.setattr(oauth_bb.ub, "oauth_support", True)

    def generated(application):
        generated_targets.append(application)
        items = []
        for offset, name in enumerate(("github", "google", "generic")):
            blueprint = Blueprint(f"fallback_{name}_{offset}", __name__)
            application.register_blueprint(blueprint)
            items.append({"blueprint": blueprint, "id": offset})
        return items

    monkeypatch.setattr(oauth_bb, "generate_oauth_blueprints", generated)
    monkeypatch.setattr(oauth_bb, "_register_auto_redirect_hooks", lambda *_args: None)
    oauth_bb.init_oauth_blueprints()
    assert generated_targets == [held]


@pytest.mark.unit
@pytest.mark.parametrize("kobo_available", [False, True])
def test_register_blueprints_preserves_order_on_each_factory_product(
    monkeypatch, kobo_available
):
    """The extracted seam registers the same ordered route surface on both apps."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    first = cps.create_app(cps.config, services)
    second = cps.create_app(cps.config, services)

    from cps import kobo as kobo_module
    from cps.main import register_blueprints

    monkeypatch.setattr(kobo_module, "get_kobo_activated", lambda: kobo_available)
    register_blueprints(first)
    register_blueprints(second)

    expected_without_generated_oauth = [
        "switch_theme", "library_refresh", "convert_library", "epub_fixer",
        "cover_enforcer_ui", "cwa_stats", "cwa_check_status", "cwa_settings",
        "cwa_logs", "profile_pictures", "cwa_internal", "search", "tasks",
        "web", "opds", "jinjia", "about", "shelf", "admin", "remotelogin",
        "metadata", "gdrive", "edit-book", "cover_picker", "cover_preview_bp",
        "annotations", "kosync", "duplicates", "api_v1", "spa", "oauth",
    ]
    expected = list(expected_without_generated_oauth)
    if kobo_available:
        expected[-1:-1] = [
            "kobo", "kobo_auth", "readingservices_api_v3",
            "readingservices_userstorage",
        ]
    assert list(first.blueprints) == expected
    assert list(second.blueprints) == expected
    assert len(list(first.url_map.iter_rules())) == len(list(second.url_map.iter_rules()))
    assert _hook_counts(first) == _hook_counts(second)
    assert _hook_counts(first)["error_handlers"] == 36


@pytest.mark.unit
def test_generated_oauth_blueprints_are_fresh_for_each_app(monkeypatch):
    """Provider blueprints cannot be carried from one factory product to the next."""
    from cps import oauth_bb

    monkeypatch.setattr(oauth_bb.ub, "oauth_support", True)
    monkeypatch.setattr(oauth_bb, "oauthblueprints", [{"stale": True}])

    def generate(application):
        from flask import Blueprint
        generated = []
        for name in ("github", "google", "generic"):
            blueprint = Blueprint(name, __name__)
            application.register_blueprint(blueprint, url_prefix="/login")
            generated.append({"blueprint": blueprint})
        return generated

    monkeypatch.setattr(oauth_bb, "generate_oauth_blueprints", generate)
    monkeypatch.setattr(oauth_bb, "_register_auto_redirect_hooks", lambda *_args: None)

    first = Flask("oauth-first")
    second = Flask("oauth-second")
    oauth_bb.init_oauth_blueprints(first)
    oauth_bb.init_oauth_blueprints(second)

    assert list(first.blueprints) == ["github", "google", "generic"]
    assert list(second.blueprints) == ["github", "google", "generic"]


@pytest.mark.unit
def test_first_apps_oauth_receiver_keeps_its_provider_after_app_two(monkeypatch):
    """An app A OAuth callback must never bind using app B's provider ID."""
    from cps import oauth_bb

    batches = iter((100, 200))

    def generated(application):
        batch = next(batches)
        items = []
        for offset, name in enumerate(("github", "google", "generic")):
            blueprint = Blueprint(f"{name}_{batch}", __name__)
            application.register_blueprint(blueprint)
            items.append({"blueprint": blueprint, "id": batch + offset})
        return items

    monkeypatch.setattr(oauth_bb.ub, "oauth_support", True)
    monkeypatch.setattr(oauth_bb, "oauthblueprints", [])
    monkeypatch.setattr(oauth_bb, "generate_oauth_blueprints", generated)
    monkeypatch.setattr(oauth_bb, "_register_auto_redirect_hooks", lambda *_args: None)
    update_token = MagicMock()
    monkeypatch.setattr(oauth_bb, "oauth_update_token", update_token)
    monkeypatch.setattr(oauth_bb, "bind_oauth_or_register", lambda *_args: None)

    first = Flask("oauth-signal-first")
    first.secret_key = "test"
    oauth_bb.init_oauth_blueprints(first)
    first_blueprint = first.blueprints["github_100"]
    first_receiver = list(oauth_authorized.receivers_for(first_blueprint))[0]

    second = Flask("oauth-signal-second")
    second.secret_key = "test"
    oauth_bb.init_oauth_blueprints(second)
    second_blueprint = second.blueprints["github_200"]
    second_receiver = list(oauth_authorized.receivers_for(second_blueprint))[0]
    assert oauth_bb.oauthblueprints[0]["id"] == 100
    with first.app_context():
        assert oauth_bb.get_oauth_blueprints()[0]["id"] == 100
    with second.app_context():
        assert oauth_bb.get_oauth_blueprints()[0]["id"] == 200

    response = SimpleNamespace(ok=True, json=lambda: {"id": "account-1"})
    sender = SimpleNamespace(name="github_100", session=SimpleNamespace(get=lambda *_: response))
    with first.test_request_context("/"):
        assert first_receiver(sender, {"access_token": "token"}) is False
    second_sender = SimpleNamespace(
        name="github_200", session=SimpleNamespace(get=lambda *_: response)
    )
    with second.test_request_context("/"):
        assert second_receiver(second_sender, {"access_token": "token"}) is False

    assert update_token.call_args_list == [
        (("100", {"access_token": "token"}, "account-1"), {}),
        (("200", {"access_token": "token"}, "account-1"), {}),
    ]


@pytest.mark.unit
def test_repeated_blueprint_registration_is_rejected_before_partial_work(monkeypatch):
    """A duplicate registration attempt fails before changing hooks, routes, or order."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    application = cps.create_app(cps.config, services)
    from cps import kobo as kobo_module
    from cps.main import register_blueprints

    monkeypatch.setattr(kobo_module, "get_kobo_activated", lambda: False)
    register_blueprints(application)
    before = (
        list(application.blueprints),
        list(application.url_map.iter_rules()),
        _hook_counts(application),
    )

    with pytest.raises(RuntimeError, match="already registered"):
        register_blueprints(application)

    assert list(application.blueprints) == before[0]
    assert list(application.url_map.iter_rules()) == before[1]
    assert _hook_counts(application) == before[2]


@pytest.mark.unit
def test_kobo_retention_starts_between_kobo_and_oauth_registration(monkeypatch):
    """Kobo retention keeps its historical place immediately before OAuth."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    application = cps.create_app(cps.config, services)
    from cps import kobo as kobo_module
    from cps.main import register_blueprints
    from cps.services import kobo_patch_spool

    monkeypatch.setattr(kobo_module, "get_kobo_activated", lambda: True)
    events = []
    real_register = application.register_blueprint

    def recording_register(blueprint, *args, **kwargs):
        events.append(blueprint.name)
        return real_register(blueprint, *args, **kwargs)

    monkeypatch.setattr(application, "register_blueprint", recording_register)
    monkeypatch.setattr(
        kobo_patch_spool,
        "start_retention_maintenance",
        lambda: events.append("kobo_retention"),
    )

    register_blueprints(application)

    assert events[-3:] == [
        "readingservices_userstorage",
        "kobo_retention",
        "oauth",
    ]


@pytest.mark.unit
def test_unit_preload_propagates_real_package_import_failure():
    """A real-package preload error must abort instead of grading cps stubs."""
    conftest_path = str(Path(cps.constants.BASE_DIR) / "tests" / "unit" / "conftest.py")
    script = """
import builtins
import runpy
real_import = builtins.__import__
def failing_import(name, *args, **kwargs):
    if name == 'cps':
        raise RuntimeError('deliberate cps preload failure')
    return real_import(name, *args, **kwargs)
builtins.__import__ = failing_import
runpy.run_path(%r, run_name='preload_probe')
""" % conftest_path

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "deliberate cps preload failure" in result.stderr


@pytest.mark.unit
def test_unit_preload_keeps_unrelated_services_failure_policy():
    """Only a failed real-cps preload is fatal; optional service preload stays best-effort."""
    conftest_path = str(Path(cps.constants.BASE_DIR) / "tests" / "unit" / "conftest.py")
    script = """
import builtins
import runpy
real_import = builtins.__import__
def failing_import(name, *args, **kwargs):
    if name == 'cps.services':
        raise RuntimeError('unrelated services preload failure')
    return real_import(name, *args, **kwargs)
builtins.__import__ = failing_import
runpy.run_path(%r, run_name='preload_probe')
print('completed')
""" % conftest_path

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().endswith("completed")
