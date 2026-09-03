# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression contract for the Hardcover configuration cluster (#897–#900).

These tests deliberately exercise behavior where possible and use source pins
only for the DOM invariant that browsers enforce (unique IDs / form nesting).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import flask
import jinja2
import pytest
from lxml import html


REPO_ROOT = Path(__file__).resolve().parents[2]


def _bare_config():
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.config_hardcover_token = None
    cfg.config_hardcover_sync = False
    cfg.config_hardcover_sync_migrated = False
    return cfg


@pytest.fixture(autouse=True)
def _clean_hardcover_env(monkeypatch):
    for name in (
        "HARDCOVER_TOKEN",
        "HARDCOVER_TOKEN_FILE",
        "HARDCOVER_SYNC_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_token_source_distinguishes_database_environment_file_and_none(
    monkeypatch, tmp_path
):
    cfg = _bare_config()
    assert cfg.hardcover_token_source() is None

    secret = tmp_path / "hardcover-token"
    secret.write_text("file-value\n", encoding="utf-8")
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(secret))
    assert cfg.hardcover_token_source() == "HARDCOVER_TOKEN_FILE"

    monkeypatch.setenv("HARDCOVER_TOKEN", "env-value")
    assert cfg.hardcover_token_source() == "HARDCOVER_TOKEN"

    cfg.config_hardcover_token = "database-value"
    assert cfg.hardcover_token_source() == "database"


def test_whitespace_database_token_falls_through_to_environment(monkeypatch):
    cfg = _bare_config()
    cfg.config_hardcover_token = "   \t"
    monkeypatch.setenv("HARDCOVER_TOKEN", " environment-value ")

    assert cfg.resolved_hardcover_token() == "environment-value"
    assert cfg.hardcover_token_source() == "HARDCOVER_TOKEN"


def test_higher_priority_token_sources_do_not_read_the_secret_file(monkeypatch):
    from cps import config_sql

    cfg = _bare_config()
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", "/slow-or-unavailable/secret")
    monkeypatch.setattr(
        config_sql,
        "_read_secret_file",
        lambda path: pytest.fail("secret file was read despite a higher-priority token"),
    )

    cfg.config_hardcover_token = "database-value"
    assert cfg.resolved_hardcover_token() == "database-value"

    cfg.config_hardcover_token = " "
    monkeypatch.setenv("HARDCOVER_TOKEN", "environment-value")
    assert cfg.resolved_hardcover_token() == "environment-value"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_sync_environment_override_is_strict_and_case_insensitive(
    monkeypatch, raw, expected
):
    cfg = _bare_config()
    cfg.config_hardcover_sync = not expected
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", raw.upper())

    assert cfg.hardcover_sync_enabled() is expected
    assert cfg.hardcover_sync_source() == "HARDCOVER_SYNC_ENABLED"


def test_invalid_sync_environment_override_falls_back_to_database(monkeypatch, caplog):
    cfg = _bare_config()
    cfg.config_hardcover_sync = True
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", "sometimes")

    assert cfg.hardcover_sync_enabled() is True
    assert cfg.hardcover_sync_source() == "database"
    assert "HARDCOVER_SYNC_ENABLED" in caplog.text


def test_first_migration_preserves_either_preexisting_enable_flag(monkeypatch):
    cfg = _bare_config()
    saved = []
    monkeypatch.setattr(cfg, "save", lambda: saved.append(True))

    effective = cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True)

    assert effective is True
    assert cfg.config_hardcover_sync is True
    assert cfg.config_hardcover_sync_migrated is True
    assert saved == [True]


def test_completed_migration_never_reimports_stale_legacy_true(monkeypatch):
    cfg = _bare_config()
    cfg.config_hardcover_sync = False
    cfg.config_hardcover_sync_migrated = True
    monkeypatch.setattr(cfg, "save", lambda: pytest.fail("migration saved twice"))

    assert cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True) is False


def test_reconciliation_persists_across_real_sqlite_restart(tmp_path, monkeypatch):
    """The one-time marker must survive a process restart in a real app.db."""
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from cps import config_sql

    monkeypatch.setenv("FLASK_DEBUG", "1")
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    Session = sessionmaker(bind=engine)
    session = Session()
    key = Fernet.generate_key()
    config_sql.load_configuration(session, key)

    first = config_sql.ConfigSQL()
    first._session = session
    first._settings = None
    first._fernet = Fernet(key)
    first.load()
    assert first.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True) is True
    assert session.query(config_sql._Settings).one().config_hardcover_sync_migrated is True
    session.close()

    restarted_session = Session()
    restarted = config_sql.ConfigSQL()
    restarted._session = restarted_session
    restarted._settings = None
    restarted._fernet = Fernet(key)
    restarted.load()

    # Simulate an operator disabling the canonical setting while an older
    # cwa.db still contains true. The stale legacy value must not resurrect.
    restarted.config_hardcover_sync = False
    restarted.save()
    assert restarted.reconcile_hardcover_sync(legacy_auto_fetch_enabled=True) is False

    restarted_session.close()
    final_session = Session()
    final = final_session.query(config_sql._Settings).one()
    assert final.config_hardcover_sync is False
    assert final.config_hardcover_sync_migrated is True
    final_session.close()
    engine.dispose()


def test_environment_override_is_effective_but_not_persisted_by_migration(monkeypatch):
    cfg = _bare_config()
    monkeypatch.setenv("HARDCOVER_SYNC_ENABLED", "true")
    monkeypatch.setattr(cfg, "save", lambda: None)

    assert cfg.reconcile_hardcover_sync(legacy_auto_fetch_enabled=False) is True
    assert cfg.config_hardcover_sync is False
    assert cfg.config_hardcover_sync_migrated is True


def test_legacy_rollback_mirror_tracks_persisted_value_not_env_override(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    writes = []

    class FakeDB:
        def get_cwa_settings(self):
            return {"hardcover_auto_fetch_enabled": False}

        def execute_write(self, query, params):
            writes.append((query, params))

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FakeDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)

    cfg = SimpleNamespace(
        config_hardcover_sync=True,
        # Models HARDCOVER_SYNC_ENABLED=false while the stored fallback is true.
        reconcile_hardcover_sync=lambda legacy_auto_fetch_enabled: False,
        hardcover_sync_enabled=lambda: False,
    )
    monkeypatch.setattr(schedule, "config", cfg)

    effective, _settings = schedule.reconcile_hardcover_configuration()

    assert effective is False
    assert writes == [
        ("UPDATE cwa_settings SET hardcover_auto_fetch_enabled = ?", (1,))
    ]


def test_cwa_database_system_exit_degrades_to_app_database_fallback(monkeypatch, caplog):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    class FailingDB:
        def __init__(self):
            raise SystemExit(0)

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FailingDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)
    monkeypatch.setattr(
        schedule,
        "config",
        SimpleNamespace(hardcover_sync_enabled=lambda: True),
    )

    assert schedule.reconcile_hardcover_configuration() == (True, None)
    assert "Unable to reconcile Hardcover configuration" in caplog.text


def test_scheduler_skips_job_when_cwa_settings_are_unavailable(monkeypatch, caplog):
    from types import SimpleNamespace

    import cps.schedule as schedule

    monkeypatch.setattr(
        schedule,
        "reconcile_hardcover_configuration",
        lambda: (True, None),
    )
    monkeypatch.setattr(
        schedule,
        "config",
        SimpleNamespace(
            hardcover_sync_source=lambda: "database",
            resolved_hardcover_token=lambda: "present-not-logged",
            hardcover_token_source=lambda: "database",
        ),
    )
    jobs = []
    schedule._schedule_hardcover_auto_fetch(
        SimpleNamespace(schedule_task=lambda *args, **kwargs: jobs.append(True)),
        None,
    )

    assert jobs == []
    assert "CWA settings are unavailable" in caplog.text


def test_startup_reconciles_once_and_passes_the_result_to_scheduling():
    init_source = (REPO_ROOT / "cps/__init__.py").read_text(encoding="utf-8")
    startup = init_source.split("from .schedule import", 1)[1].split(
        "register_startup_tasks()", 1
    )[0]
    schedule_source = (REPO_ROOT / "cps/schedule.py").read_text(encoding="utf-8")
    register = schedule_source.split("def register_scheduled_tasks", 1)[1].split(
        "def register_startup_tasks", 1
    )[0]

    assert "reconcile_hardcover_configuration()" not in startup
    assert "hardcover_configuration = reconcile_hardcover_configuration()" in register
    assert "_schedule_hardcover_auto_fetch(" in register
    assert "hardcover_configuration" in register.split(
        "_schedule_hardcover_auto_fetch(", 1
    )[1]


def test_admin_template_has_one_sync_control_and_ungated_token_status():
    template = (REPO_ROOT / "cps/templates/config_edit.html").read_text(
        encoding="utf-8"
    )

    assert template.count('id="config_hardcover_sync"') == 1
    assert template.count('name="config_hardcover_sync"') == 1
    assert 'data-related="hardcover-settings"' not in template

    token_pos = template.index('id="config_hardcover_token"')
    status_pos = template.index("hardcover_token_status")
    sync_pos = template.index('id="config_hardcover_sync"')
    assert token_pos > sync_pos
    assert status_pos > sync_pos


def test_admin_save_has_one_hardcover_sync_coercion_path():
    source = (REPO_ROOT / "cps/admin.py").read_text(encoding="utf-8")
    assert source.count('_config_checkbox(to_save, "config_hardcover_sync")') == 1
    assert '_config_checkbox_int(to_save, "config_hardcover_sync")' not in source
    assert 'hardcover_sync_source() == "database"' in source
    helper = source.split("def _configuration_update_helper():", 1)[1].split(
        "def _configuration_result", 1
    )[0]
    assert "prev_hardcover_sync = config.hardcover_sync_enabled()" in helper
    assert "prev_hardcover_token_available = bool(config.resolved_hardcover_token())" in helper
    assert "schedule.refresh_hardcover_auto_fetch()" in helper
    assert "schedule.register_scheduled_tasks" not in helper
    assert "hardcover_token_available != prev_hardcover_token_available" in helper


def test_auto_fetch_task_rechecks_effective_enable_before_database_or_network(monkeypatch, caplog):
    from types import SimpleNamespace

    from cps.tasks import auto_hardcover_id

    monkeypatch.setattr(
        auto_hardcover_id,
        "config",
        SimpleNamespace(hardcover_sync_enabled=lambda: False),
    )
    monkeypatch.setattr(
        auto_hardcover_id.db,
        "CalibreDB",
        lambda *args, **kwargs: pytest.fail("disabled task opened the database"),
    )
    task = auto_hardcover_id.TaskAutoHardcoverID()
    completed = []
    monkeypatch.setattr(task, "_handleSuccess", lambda: completed.append(True))

    task.run(None)

    assert completed == [True]
    assert "disabled" in caplog.text.lower()


def test_auto_fetch_task_stops_when_disabled_after_it_started(monkeypatch, caplog):
    from types import SimpleNamespace

    from cps.tasks import auto_hardcover_id

    states = iter((True, True, False))
    monkeypatch.setattr(
        auto_hardcover_id,
        "config",
        SimpleNamespace(
            hardcover_sync_enabled=lambda: next(states),
            resolved_hardcover_token=lambda: "present-not-logged",
        ),
    )
    fake_calibre = SimpleNamespace(session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(auto_hardcover_id.db, "CalibreDB", lambda **kwargs: fake_calibre)
    task = auto_hardcover_id.TaskAutoHardcoverID()
    monkeypatch.setattr(task, "_get_books_without_hardcover_id", lambda: [7])
    monkeypatch.setattr(task, "_get_books_for_batch", lambda ids: [SimpleNamespace(id=7)])
    monkeypatch.setattr(
        task,
        "_process_book",
        lambda book: pytest.fail("task processed a book after sync was disabled"),
    )
    completed = []
    monkeypatch.setattr(task, "_handleSuccess", lambda: completed.append(True))

    task.run(None)

    assert completed == [True]
    assert "stopped" in caplog.text.lower()


def test_manual_auto_fetch_endpoint_checks_effective_enable_first():
    source = (REPO_ROOT / "cps/admin.py").read_text(encoding="utf-8")
    endpoint = source.split("def trigger_hardcover_auto_fetch():", 1)[1].split(
        "@admi.route", 1
    )[0]
    gate_pos = endpoint.index("config.hardcover_sync_enabled()")
    token_pos = endpoint.index("config.resolved_hardcover_token()")
    assert gate_pos < token_pos


def test_cwa_schedule_changes_refresh_jobs_without_restart():
    source = (REPO_ROOT / "cps/cwa_functions.py").read_text(encoding="utf-8")
    endpoint = source.split("def set_cwa_settings():", 1)[1].split(
        "def get_next_duplicate_scan_run", 1
    )[0]
    assert "schedule.refresh_hardcover_auto_fetch()" in endpoint
    assert "schedule.register_scheduled_tasks" not in endpoint


def test_hardcover_refresh_preserves_unrelated_pending_jobs(monkeypatch):
    from types import SimpleNamespace

    import cps.schedule as schedule

    unrelated = SimpleNamespace(id="auto-send-17", name="rehydrated auto-send 17")
    hardcover = SimpleNamespace(id="hardcover-old", name="hardcover auto-fetch")
    jobs = [unrelated, hardcover]
    removed = []

    class FakeScheduler:
        def get_jobs(self):
            return list(jobs)

        def remove_job(self, job_id):
            removed.append(job_id)

    monkeypatch.setattr(schedule, "BackgroundScheduler", lambda: FakeScheduler())
    scheduled = []
    monkeypatch.setattr(
        schedule,
        "_schedule_hardcover_auto_fetch",
        lambda scheduler, timezone_info: scheduled.append((scheduler, timezone_info)),
    )

    schedule.refresh_hardcover_auto_fetch()

    assert removed == ["hardcover-old"]
    assert len(scheduled) == 1


def test_concurrent_hardcover_refreshes_leave_one_recurring_job(monkeypatch):
    import threading
    from types import SimpleNamespace

    import cps.schedule as schedule

    jobs = []
    snapshots = threading.Barrier(2)

    class FakeScheduler:
        def get_jobs(self):
            snapshot = list(jobs)
            try:
                snapshots.wait(timeout=0.1)
            except threading.BrokenBarrierError:
                pass
            return snapshot

        def remove_job(self, job_id):
            jobs[:] = [job for job in jobs if job.id != job_id]

    monkeypatch.setattr(schedule, "BackgroundScheduler", lambda: FakeScheduler())
    monkeypatch.setattr(
        schedule,
        "_schedule_hardcover_auto_fetch",
        lambda scheduler, timezone_info: jobs.append(
            SimpleNamespace(
                id=f"hardcover-{len(jobs)}",
                name="hardcover auto-fetch",
            )
        ),
    )

    threads = [threading.Thread(target=schedule.refresh_hardcover_auto_fetch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(jobs) == 1


def test_applying_cwa_defaults_restores_the_rollback_mirror():
    from cps import cwa_functions

    writes = []

    class FakeDB:
        def execute_write(self, query, params):
            writes.append((query, params))

    missing = object()
    original = getattr(cwa_functions.config, "config_hardcover_sync", missing)
    try:
        cwa_functions.config.config_hardcover_sync = True
        cwa_functions._mirror_hardcover_sync_for_rollback(FakeDB())
    finally:
        if original is missing:
            del cwa_functions.config.config_hardcover_sync
        else:
            cwa_functions.config.config_hardcover_sync = original

    assert writes == [
        ("UPDATE cwa_settings SET hardcover_auto_fetch_enabled = ?", (1,))
    ]

    source = (REPO_ROOT / "cps/cwa_functions.py").read_text(encoding="utf-8")
    defaults_call = source.index("cwa_db.set_default_settings(force=True)")
    mirror_call = source.index("_mirror_hardcover_sync_for_rollback(cwa_db)", defaults_call)
    assert mirror_call > defaults_call


def test_scheduler_logs_disabled_and_missing_token_as_distinct_states(
    monkeypatch, caplog
):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    class FakeDB:
        def get_cwa_settings(self):
            return {
                "hardcover_auto_fetch_enabled": False,
                "hardcover_auto_fetch_schedule": "weekly",
            }

        def execute_write(self, *_args, **_kwargs):
            return None

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FakeDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)

    cfg = SimpleNamespace(
        config_hardcover_sync=False,
        reconcile_hardcover_sync=lambda legacy_auto_fetch_enabled: False,
        hardcover_sync_enabled=lambda: False,
        hardcover_sync_source=lambda: "database",
        resolved_hardcover_token=lambda: "",
        hardcover_token_source=lambda: None,
    )
    monkeypatch.setattr(schedule, "config", cfg)

    schedule._schedule_hardcover_auto_fetch(SimpleNamespace(), None)

    assert "Hardcover sync is disabled" in caplog.text
    assert "Hardcover token is not configured" in caplog.text


def test_scheduler_logs_presence_and_source_without_token_value(monkeypatch, caplog):
    import sys
    from types import ModuleType, SimpleNamespace

    import cps.schedule as schedule

    token = "must-never-appear-in-logs"

    class FakeDB:
        def get_cwa_settings(self):
            return {
                "hardcover_auto_fetch_enabled": True,
                "hardcover_auto_fetch_schedule": "weekly",
                "hardcover_auto_fetch_schedule_day": "sunday",
                "hardcover_auto_fetch_schedule_hour": 2,
                "hardcover_auto_fetch_min_confidence": 0.85,
                "hardcover_auto_fetch_batch_size": 50,
                "hardcover_auto_fetch_rate_limit": 5.0,
            }

        def execute_write(self, *_args, **_kwargs):
            return None

    fake_module = ModuleType("cwa_db")
    fake_module.CWA_DB = FakeDB
    monkeypatch.setitem(sys.modules, "cwa_db", fake_module)

    cfg = SimpleNamespace(
        config_hardcover_sync=False,
        reconcile_hardcover_sync=lambda legacy_auto_fetch_enabled: True,
        hardcover_sync_enabled=lambda: True,
        hardcover_sync_source=lambda: "HARDCOVER_SYNC_ENABLED",
        resolved_hardcover_token=lambda: token,
        hardcover_token_source=lambda: "HARDCOVER_TOKEN",
    )
    monkeypatch.setattr(schedule, "config", cfg)

    jobs = []
    scheduler = SimpleNamespace(
        schedule_task=lambda *args, **kwargs: jobs.append((args, kwargs))
    )
    schedule._schedule_hardcover_auto_fetch(scheduler, None)

    assert "Hardcover sync is enabled via HARDCOVER_SYNC_ENABLED" in caplog.text
    assert "Hardcover token is configured via HARDCOVER_TOKEN" in caplog.text
    assert token not in caplog.text
    assert len(jobs) == 1


def _enabled_hardcover_config():
    return SimpleNamespace(
        config_hardcover_sync=True,
        hardcover_sync_source=lambda: "database",
        resolved_hardcover_token=lambda: "present-not-logged",
        hardcover_token_source=lambda: "database",
    )


def test_never_refresh_removes_auto_fetch_without_disabling_sync_or_other_jobs(
    monkeypatch, caplog
):
    import cps.schedule as schedule

    unrelated = SimpleNamespace(id="duplicate-scan", name="duplicate scan")
    old_hardcover = SimpleNamespace(
        id="hardcover-old", name="hardcover auto-fetch"
    )
    jobs = [unrelated, old_hardcover]

    class FakeScheduler:
        def get_jobs(self):
            return list(jobs)

        def remove_job(self, job_id):
            jobs[:] = [job for job in jobs if job.id != job_id]

        def schedule_task(self, _task, **kwargs):
            jobs.append(
                SimpleNamespace(id="hardcover-new", name=kwargs["name"])
            )

    cfg = _enabled_hardcover_config()
    monkeypatch.setattr(schedule, "config", cfg)
    monkeypatch.setattr(schedule, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(
        schedule,
        "reconcile_hardcover_configuration",
        lambda: (
            True,
            {"hardcover_auto_fetch_schedule": "never"},
        ),
    )
    caplog.set_level("INFO")

    schedule.refresh_hardcover_auto_fetch()

    assert cfg.config_hardcover_sync is True
    assert [(job.id, job.name) for job in jobs] == [
        ("duplicate-scan", "duplicate scan")
    ]
    assert "Hardcover auto-fetch is off by configuration" in caplog.text


def test_unknown_auto_fetch_schedule_warns_and_falls_back_to_weekly(
    monkeypatch, caplog
):
    import cps.schedule as schedule

    cfg = _enabled_hardcover_config()
    monkeypatch.setattr(schedule, "config", cfg)
    jobs = []
    scheduler = SimpleNamespace(
        schedule_task=lambda *args, **kwargs: jobs.append((args, kwargs))
    )

    schedule._schedule_hardcover_auto_fetch(
        scheduler,
        None,
        configuration=(
            True,
            {
                "hardcover_auto_fetch_schedule": "fortnightly",
                "hardcover_auto_fetch_schedule_day": "sunday",
                "hardcover_auto_fetch_schedule_hour": 2,
            },
        ),
    )

    assert len(jobs) == 1
    assert "fortnightly" in caplog.text
    assert "weekly" in caplog.text
    trigger = jobs[0][1]["trigger"]
    assert str(trigger).startswith("cron[")
    assert "day_of_week='sun'" in str(trigger)
    assert "hour='2'" in str(trigger)


def test_missing_auto_fetch_schedule_keeps_existing_weekly_default(monkeypatch):
    import cps.schedule as schedule

    assert schedule.DEFAULT_HARDCOVER_AUTO_FETCH_SCHEDULE == "weekly"
    assert "weekly" in schedule.HARDCOVER_AUTO_FETCH_SCHEDULES

    monkeypatch.setattr(schedule, "config", _enabled_hardcover_config())
    jobs = []
    scheduler = SimpleNamespace(
        schedule_task=lambda *args, **kwargs: jobs.append((args, kwargs))
    )

    schedule._schedule_hardcover_auto_fetch(
        scheduler,
        None,
        configuration=(True, {}),
    )

    assert len(jobs) == 1
    trigger = jobs[0][1]["trigger"]
    assert "day_of_week='sun'" in str(trigger)
    assert "hour='2'" in str(trigger)


class _ScheduleSettingsDB:
    stored = {
        "auto_convert_target_format": "epub",
        "hardcover_auto_fetch_schedule": "daily",
    }
    updates = []

    def __init__(self):
        self.cwa_default_settings = dict(self.stored)
        self.cwa_settings = dict(self.stored)

    def update_cwa_settings(self, settings):
        self.__class__.updates.append(dict(settings))
        self.__class__.stored.update(settings)

    def get_cwa_settings(self):
        return dict(self.__class__.stored)


@pytest.fixture
def schedule_settings_client(monkeypatch):
    from cps import cwa_functions, schedule

    _ScheduleSettingsDB.stored = {
        "auto_convert_target_format": "epub",
        "hardcover_auto_fetch_schedule": "daily",
    }
    _ScheduleSettingsDB.updates = []
    monkeypatch.setattr(cwa_functions, "CWA_DB", _ScheduleSettingsDB)
    monkeypatch.setattr(cwa_functions, "INTEGER_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "FLOAT_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "JSON_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "_", lambda text, **_kwargs: text)
    monkeypatch.setattr(cwa_functions.config, "config_kobo_sync_magic_shelves", False, raising=False)
    monkeypatch.setattr(cwa_functions.config, "config_hardcover_sync", True, raising=False)
    monkeypatch.setattr(cwa_functions.config, "save", lambda: None)
    monkeypatch.setattr(cwa_functions.config, "resolved_hardcover_token", lambda: None)
    monkeypatch.setattr(schedule, "refresh_hardcover_auto_fetch", lambda: None)
    monkeypatch.setattr(cwa_functions, "get_next_duplicate_scan_run", lambda _settings: None)
    monkeypatch.setattr(
        cwa_functions,
        "render_title_template",
        lambda _template, **context: {"settings": context["cwa_settings"]},
    )

    app = flask.Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(cwa_functions.cwa_settings)
    app.view_functions["cwa_settings.set_cwa_settings"] = inspect.unwrap(
        cwa_functions.set_cwa_settings
    )
    return app.test_client(), _ScheduleSettingsDB


def test_settings_writer_keeps_previous_schedule_for_invalid_post(
    schedule_settings_client, caplog
):
    client, settings_db = schedule_settings_client

    response = client.post(
        "/cwa-settings",
        data={
            "settings_action": "save",
            "auto_convert_target_format": "epub",
            "hardcover_auto_fetch_schedule": "typo-value",
        },
    )

    assert response.status_code == 200
    assert settings_db.updates[-1]["hardcover_auto_fetch_schedule"] == "daily"
    assert settings_db.stored["hardcover_auto_fetch_schedule"] == "daily"
    assert "Ignoring unrecognized Hardcover auto-fetch schedule 'typo-value'" in caplog.text


def test_settings_writer_silently_preserves_schedule_when_field_is_absent(
    schedule_settings_client, caplog
):
    client, settings_db = schedule_settings_client

    response = client.post(
        "/cwa-settings",
        data={
            "settings_action": "save",
            "auto_convert_target_format": "epub",
        },
    )

    assert response.status_code == 200
    assert settings_db.updates[-1]["hardcover_auto_fetch_schedule"] == "daily"
    assert settings_db.stored["hardcover_auto_fetch_schedule"] == "daily"
    assert "Ignoring unrecognized Hardcover auto-fetch schedule" not in caplog.text


def _rendered_auto_fetch_schedule_value(cwa_settings):
    template = (REPO_ROOT / "cps/templates/cwa_settings.html").read_text(
        encoding="utf-8"
    )
    select_start = template.index(
        '<select name="hardcover_auto_fetch_schedule"'
    )
    select_end = template.index("</select>", select_start) + len("</select>")
    select_template = jinja2.Environment(autoescape=True).from_string(
        template[select_start:select_end]
    )
    rendered = select_template.render(
        _=lambda text: text,
        cwa_settings=cwa_settings,
        hardcover_token_available=True,
    )
    select = html.fromstring(rendered)
    options = select.xpath(".//option")
    explicitly_selected = select.xpath(".//option[@selected]")
    effective_option = explicitly_selected[0] if explicitly_selected else options[0]
    return (
        [option.get("value") for option in explicitly_selected],
        effective_option.get("value"),
    )


@pytest.mark.parametrize(
    ("stored_value", "remove_stored_key"),
    [
        pytest.param("fortnightly", False, id="unrecognized"),
        pytest.param(None, True, id="missing"),
    ],
)
def test_rendered_auto_fetch_schedule_matches_scheduler_fallback(
    schedule_settings_client, stored_value, remove_stored_key
):
    from cps import schedule

    client, settings_db = schedule_settings_client
    if remove_stored_key:
        settings_db.stored.pop("hardcover_auto_fetch_schedule")
    else:
        settings_db.stored["hardcover_auto_fetch_schedule"] = stored_value

    response = client.get("/cwa-settings")

    assert response.status_code == 200
    rendered_settings = response.get_json()["settings"]
    explicitly_selected, effective_value = _rendered_auto_fetch_schedule_value(
        rendered_settings
    )
    assert effective_value == schedule.DEFAULT_HARDCOVER_AUTO_FETCH_SCHEDULE
    assert effective_value == schedule.resolve_hardcover_auto_fetch_schedule(
        stored_value
    )
    assert explicitly_selected == [effective_value]


def test_auto_fetch_ui_has_first_off_option_and_truthful_status_combinations():
    from cps import schedule

    template = (REPO_ROOT / "cps/templates/cwa_settings.html").read_text(
        encoding="utf-8"
    )
    select = template.split(
        '<select name="hardcover_auto_fetch_schedule"', 1
    )[1].split("</select>", 1)[0]
    option_values = re.findall(r'<option value="([^"]+)"', select)
    assert option_values[0] == "never"
    assert frozenset(option_values) == schedule.HARDCOVER_AUTO_FETCH_SCHEDULES
    assert "{{_('Never (auto-fetch off)')}}" in select

    for schedule_value in ("weekly", "never"):
        explicitly_selected, effective_value = _rendered_auto_fetch_schedule_value(
            {"hardcover_auto_fetch_schedule": schedule_value}
        )
        assert effective_value == schedule_value
        assert explicitly_selected == [schedule_value]

    status_source = template.split(
        '<p class="cwa-settings-tooltip" role="status">', 1
    )[1].split("</p>", 1)[0]
    environment = jinja2.Environment(autoescape=True)
    status_template = environment.from_string(status_source)

    expected = {
        (True, "weekly"): (
            "Hardcover sync is enabled. The schedule below controls automatic ID fetching."
        ),
        (True, "never"): (
            "Hardcover auto-fetch is off. Reading-progress and annotation sync are unaffected."
        ),
        (False, "weekly"): (
            "Hardcover sync is disabled, so auto-fetch, reading-progress sync, and annotation sync are off."
        ),
        (False, "never"): (
            "Hardcover sync is disabled, and auto-fetch is off. Enable Hardcover sync separately in Basic Configuration when you want reading-progress or annotation sync."
        ),
    }
    for (sync_enabled, schedule_value), message in expected.items():
        rendered = status_template.render(
            _=lambda text: text,
            config=SimpleNamespace(
                hardcover_sync_enabled=lambda enabled=sync_enabled: enabled
            ),
            cwa_settings={"hardcover_auto_fetch_schedule": schedule_value},
        )
        assert message in rendered


def _ambiguous_hardcover_result(result_id):
    return SimpleNamespace(
        id=result_id,
        title=f"Candidate {result_id}",
        authors=["Author"],
        url="",
        cover="",
        description="",
        series="",
        series_index=None,
        publisher="",
        publishedDate="",
        identifiers={"hardcover-id": result_id},
    )


def _ambiguous_scored_results(result_id):
    return [{
        "result": _ambiguous_hardcover_result(result_id),
        "score": 0.5,
        "reason": f"ambiguous-{result_id}",
    }]


@pytest.fixture
def hardcover_queue_runtime(monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from cps import db, ub
    from cps.tasks import auto_hardcover_id as module
    from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

    app_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ub.HardcoverMatchQueue.__table__.create(app_engine)
    AppSession = sessionmaker(bind=app_engine)
    monkeypatch.setattr(module.ub, "init_db_thread", AppSession)

    calibre_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        calibre_engine,
        "connect",
        lambda connection, _record: connection.execute(
            "ATTACH DATABASE ':memory:' AS calibre"
        ),
    )
    db.Base.metadata.create_all(calibre_engine)
    CalibreSession = sessionmaker(bind=calibre_engine)
    calibre_session = CalibreSession()
    book = db.Books(
        "Ambiguous Book",
        "Ambiguous Book",
        "Author",
        datetime.now(timezone.utc),
        datetime.now(timezone.utc),
        "1.0",
        datetime.now(timezone.utc),
        "ambiguous-book",
        False,
        [],
        [],
    )
    calibre_session.add(book)
    calibre_session.commit()
    book_id = book.id
    calibre_session.close()

    monkeypatch.setattr(
        module.db,
        "CalibreDB",
        lambda **_kwargs: SimpleNamespace(session=CalibreSession()),
    )
    monkeypatch.setattr(module.config, "hardcover_sync_enabled", lambda: True)
    monkeypatch.setattr(module.config, "resolved_hardcover_token", lambda: "token")
    monkeypatch.setattr(TaskAutoHardcoverID, "_save_stats", lambda self: None)

    searches = []

    class AmbiguousProvider:
        def search(self, query):
            searches.append(query)
            return [_ambiguous_hardcover_result("candidate-1")]

        @staticmethod
        def calculate_confidence_score(**_kwargs):
            return 0.5, "ambiguous"

    monkeypatch.setattr(module, "Hardcover", AmbiguousProvider)

    runtime = SimpleNamespace(
        AppSession=AppSession,
        book_id=book_id,
        searches=searches,
    )
    try:
        yield runtime
    finally:
        app_engine.dispose()
        calibre_engine.dispose()


def test_two_ambiguous_crawls_leave_one_pending_row(
    hardcover_queue_runtime, caplog
):
    from cps import ub
    from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

    first = TaskAutoHardcoverID(batch_size=10, rate_limit_delay=0)
    second = TaskAutoHardcoverID(batch_size=10, rate_limit_delay=0)

    first.run(None)
    second.run(None)

    session = hardcover_queue_runtime.AppSession()
    try:
        pending = session.query(ub.HardcoverMatchQueue).filter_by(
            book_id=hardcover_queue_runtime.book_id,
            reviewed=0,
        ).all()
    finally:
        session.close()

    assert len(hardcover_queue_runtime.searches) == 1
    assert len(pending) == 1
    assert first.books_processed == 1
    assert second.books_processed == 0
    assert caplog.text.count(
        "Found 1 eligible books without Hardcover IDs"
    ) == 1
    assert "No books eligible for Hardcover ID auto-fetch" in caplog.text


def test_queue_for_review_refreshes_existing_pending_row(
    monkeypatch, hardcover_queue_runtime
):
    from cps import ub
    from cps.tasks import auto_hardcover_id as module
    from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

    timestamps = iter(("2026-08-01T00:00:00", "2026-09-01T00:00:00"))
    monkeypatch.setattr(
        module,
        "datetime",
        SimpleNamespace(
            utcnow=lambda: SimpleNamespace(isoformat=lambda: next(timestamps))
        ),
    )
    task = TaskAutoHardcoverID()

    task._queue_for_review(
        hardcover_queue_runtime.book_id,
        "Original title",
        "Original author",
        "original query",
        _ambiguous_scored_results("old-result"),
    )
    task._queue_for_review(
        hardcover_queue_runtime.book_id,
        "Updated title",
        "Updated author",
        "updated query",
        _ambiguous_scored_results("new-result"),
    )

    session = hardcover_queue_runtime.AppSession()
    try:
        rows = session.query(ub.HardcoverMatchQueue).filter_by(
            book_id=hardcover_queue_runtime.book_id,
            reviewed=0,
        ).all()
        assert len(rows) == 1
        assert rows[0].book_title == "Updated title"
        assert rows[0].search_query == "updated query"
        assert rows[0].created_at == "2026-09-01T00:00:00"
        assert "new-result" in rows[0].hardcover_results
        assert "ambiguous-new-result" in rows[0].confidence_scores
    finally:
        session.close()


def test_rejected_book_is_not_researched_or_requeued(hardcover_queue_runtime):
    from cps import ub
    from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID

    session = hardcover_queue_runtime.AppSession()
    session.add(ub.HardcoverMatchQueue(
        book_id=hardcover_queue_runtime.book_id,
        book_title="Ambiguous Book",
        book_authors="Author",
        search_query="Ambiguous Book Author",
        hardcover_results="[]",
        confidence_scores="[]",
        created_at="2026-08-01T00:00:00",
        reviewed=1,
        review_action="reject",
        reviewed_at="2026-08-02T00:00:00",
    ))
    session.commit()
    session.close()

    task = TaskAutoHardcoverID(batch_size=10, rate_limit_delay=0)
    task.run(None)

    session = hardcover_queue_runtime.AppSession()
    try:
        rows = session.query(ub.HardcoverMatchQueue).filter_by(
            book_id=hardcover_queue_runtime.book_id,
        ).all()
    finally:
        session.close()

    assert hardcover_queue_runtime.searches == []
    assert len(rows) == 1
    assert rows[0].reviewed == 1
    assert rows[0].review_action == "reject"


def test_pending_queue_cleanup_keeps_newest_and_all_reviewed_rows(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from cps import ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE hardcover_match_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                created_at VARCHAR NOT NULL,
                reviewed INTEGER NOT NULL,
                review_action VARCHAR
            )
        """))
        connection.execute(text("""
            INSERT INTO hardcover_match_queue
                (book_id, created_at, reviewed, review_action)
            VALUES
                (1, '2026-07-01T00:00:00', 0, NULL),
                (1, '2026-08-01T00:00:00', 0, NULL),
                (1, '2026-06-01T00:00:00', 1, 'reject'),
                (2, '2026-07-15T00:00:00', 0, NULL),
                (2, '2026-07-15T00:00:00', 0, NULL),
                (2, '2026-05-01T00:00:00', 1, 'skip')
        """))
        connection.execute(
            text(
                "INSERT INTO hardcover_match_queue "
                "(book_id, created_at, reviewed, review_action) "
                "VALUES (:book_id, :created_at, 0, NULL)"
            ),
            [
                {
                    "book_id": 100 + (index % 274),
                    "created_at": f"2026-08-01T00:00:00.{index:06d}",
                }
                for index in range(30000)
            ],
        )
    session = sessionmaker(bind=engine)()
    try:
        ub.migrate_hardcover_match_queue_dedup(engine, session)
        ub.migrate_hardcover_match_queue_dedup(engine, session)
    finally:
        session.close()

    with engine.connect() as connection:
        pending = connection.execute(text(
            "SELECT id, book_id, created_at FROM hardcover_match_queue "
            "WHERE reviewed = 0 AND book_id IN (1, 2) ORDER BY book_id"
        )).fetchall()
        pending_count = connection.execute(text(
            "SELECT COUNT(*) FROM hardcover_match_queue WHERE reviewed = 0"
        )).scalar()
        reviewed = connection.execute(text(
            "SELECT id, book_id, review_action FROM hardcover_match_queue "
            "WHERE reviewed = 1 ORDER BY id"
        )).fetchall()
        indexes = {
            row[1]: row[2]
            for row in connection.execute(text(
                "PRAGMA index_list(hardcover_match_queue)"
            )).fetchall()
        }

    assert [tuple(row) for row in pending] == [
        (2, 1, "2026-08-01T00:00:00"),
        (5, 2, "2026-07-15T00:00:00"),
    ]
    assert pending_count == 276
    assert [tuple(row) for row in reviewed] == [
        (3, 1, "reject"),
        (6, 2, "skip"),
    ]
    assert indexes["uq_hardcover_match_queue_pending_book"] == 1
    assert "ix_hardcover_match_queue_review_state_book" in indexes
    engine.dispose()
