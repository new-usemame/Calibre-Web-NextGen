# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression contract for the Hardcover configuration cluster (#897–#900).

These tests deliberately exercise behavior where possible and use source pins
only for the DOM invariant that browsers enforce (unique IDs / form nesting).
"""

from __future__ import annotations

from pathlib import Path

import pytest


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
