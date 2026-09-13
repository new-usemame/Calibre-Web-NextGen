# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The OpenRouter key and the two dollar figures either side of it.

A metadata token reads a public catalogue. This one authorises spending money
against the administrator's own account, so the tests here are about the three
ways that goes wrong: the key leaking back out of the settings it went into, an
upgrade that silently leaves the limits unset, and a form save that wipes a key
nobody meant to remove.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit

REFLOW_COLUMNS = ("config_openrouter_key_e", "config_reflow_default_tier",
                  "config_reflow_target_usd", "config_reflow_hard_cap_usd")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY_FILE", raising=False)


def _bare_config():
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.__dict__["config_openrouter_key_e"] = None
    return cfg


# ── where the key comes from ─────────────────────────────────────────────────

def test_the_administrators_key_wins_over_the_containers(monkeypatch):
    cfg = _bare_config()
    cfg.__dict__["config_openrouter_key_e"] = "sk-db"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert cfg.resolved_openrouter_key() == "sk-db"
    assert cfg.openrouter_key_source() == "database"


def test_a_key_set_only_in_the_environment_still_works(monkeypatch):
    cfg = _bare_config()
    monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-env  ")
    assert cfg.resolved_openrouter_key() == "sk-env"
    assert cfg.openrouter_key_source() == "OPENROUTER_API_KEY"


def test_a_docker_secret_file_is_read_when_nothing_else_is_set(monkeypatch, tmp_path):
    cfg = _bare_config()
    secret = tmp_path / "openrouter_key"
    secret.write_text("sk-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", str(secret))
    assert cfg.resolved_openrouter_key() == "sk-file"
    assert cfg.openrouter_key_source() == "OPENROUTER_API_KEY_FILE"


def test_no_key_anywhere_is_an_empty_answer_and_not_a_crash():
    cfg = _bare_config()
    assert cfg.resolved_openrouter_key() == ""
    assert cfg.openrouter_key_source() is None


# ── where the key must never turn up ─────────────────────────────────────────

def test_the_key_is_not_in_the_dictionary_the_settings_api_serialises():
    cfg = _bare_config()
    cfg.__dict__["config_openrouter_key_e"] = "sk-secret-value"
    assert "sk-secret-value" not in repr(cfg.to_dict())


def test_the_key_is_redacted_from_the_debug_bundle():
    from cps.debug_info import _KNOWN_SECRET_FIELDS

    assert "config_openrouter_key_e" in _KNOWN_SECRET_FIELDS


def test_the_key_is_encrypted_at_rest_like_the_other_passwords():
    """Suffixed ``_e``, which is what the load/save pair keys encryption off."""
    from cps.config_sql import _Settings

    assert hasattr(_Settings, "config_openrouter_key_e")
    assert not hasattr(_Settings, "config_openrouter_key")


# ── the upgrade ──────────────────────────────────────────────────────────────

def test_an_existing_installation_gains_the_limits_with_their_defaults(tmp_path):
    """The columns are added to a database that predates them, with real numbers.

    SQLite is happy to store the string "0.5" in a REAL column, so this asserts the
    stored *type* as well as the value: a hard cap that is text compares wrongly
    against a float and would let a job past it.
    """
    from cps import config_sql

    if sqlite3.sqlite_version_info < (3, 35):                      # pragma: no cover
        pytest.skip("ALTER TABLE ... DROP COLUMN needs SQLite 3.35+")

    path = tmp_path / "app.db"
    engine = create_engine("sqlite:///%s" % path)
    config_sql._Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for column in REFLOW_COLUMNS:
            conn.execute(sa_text("ALTER TABLE settings DROP COLUMN %s" % column))
        conn.execute(sa_text("INSERT INTO settings (id) VALUES (1)"))

    session = sessionmaker(bind=engine)()
    config_sql._migrate_table(session, config_sql._Settings)

    with engine.begin() as conn:
        present = {row[1] for row in conn.execute(sa_text("PRAGMA table_info(settings)"))}
        row = conn.execute(sa_text(
            "SELECT config_reflow_default_tier, config_reflow_target_usd,"
            " typeof(config_reflow_target_usd), config_reflow_hard_cap_usd,"
            " typeof(config_reflow_hard_cap_usd) FROM settings WHERE id = 1")).first()
    session.close()

    assert set(REFLOW_COLUMNS) <= present
    assert row[0] == "standard"
    assert row[1] == pytest.approx(0.5) and row[2] == "real"
    assert row[3] == pytest.approx(5.0) and row[4] == "real"


# ── the admin form ───────────────────────────────────────────────────────────

class _Recorder(object):
    """Just enough ConfigSQL to watch what a form save does."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def set_from_dictionary(self, dictionary, field, convertor=None, default=None,
                            encode=None):
        value = dictionary.get(field, default)
        if value is None:
            return False
        if convertor is not None:
            value = convertor(value)
        if self.__dict__.get(field) == value:
            return False
        self.__dict__[field] = value
        return True


def _saver(monkeypatch, **stored):
    from cps import admin as admin_mod

    recorder = _Recorder(config_openrouter_key_e="sk-already-here",
                         config_reflow_default_tier="standard",
                         config_reflow_target_usd=0.5,
                         config_reflow_hard_cap_usd=5.0, **stored)
    monkeypatch.setattr(admin_mod, "config", recorder)
    return recorder, admin_mod


def test_saving_the_settings_form_without_retyping_the_key_keeps_it(monkeypatch):
    """The field is never rendered back, so "empty" means "unchanged", not "delete"."""
    recorder, admin_mod = _saver(monkeypatch)
    admin_mod._save_openrouter_key({"config_openrouter_key_e": ""})
    assert recorder.config_openrouter_key_e == "sk-already-here"


def test_a_key_that_was_typed_in_replaces_the_one_that_was_there(monkeypatch):
    recorder, admin_mod = _saver(monkeypatch)
    admin_mod._save_openrouter_key({"config_openrouter_key_e": "  sk-new-one  "})
    assert recorder.config_openrouter_key_e == "sk-new-one"


def test_clearing_the_key_takes_a_deliberate_tick(monkeypatch):
    recorder, admin_mod = _saver(monkeypatch)
    admin_mod._save_openrouter_key({"config_openrouter_key_e": "",
                                    "config_openrouter_key_clear": "on"})
    assert recorder.config_openrouter_key_e == ""


def test_the_settings_form_actually_calls_the_key_saver():
    """A helper nothing calls protects nothing."""
    import inspect

    from cps import admin as admin_mod

    source = inspect.getsource(admin_mod._configuration_update_helper)
    assert "_save_openrouter_key(to_save)" in source


def test_a_dollar_field_that_is_not_a_number_leaves_the_limit_alone(monkeypatch):
    from cps import admin as admin_mod

    recorder = _Recorder(config_reflow_hard_cap_usd=5.0)
    monkeypatch.setattr(admin_mod, "config", recorder)

    admin_mod._config_float({"config_reflow_hard_cap_usd": "five dollars"},
                            "config_reflow_hard_cap_usd")
    assert recorder.config_reflow_hard_cap_usd == 5.0

    admin_mod._config_float({"config_reflow_hard_cap_usd": "2.50"},
                            "config_reflow_hard_cap_usd")
    assert recorder.config_reflow_hard_cap_usd == pytest.approx(2.5)


def test_a_negative_cap_is_not_a_refund(monkeypatch):
    from cps import admin as admin_mod

    recorder = _Recorder(config_reflow_hard_cap_usd=5.0)
    monkeypatch.setattr(admin_mod, "config", recorder)
    admin_mod._config_float({"config_reflow_hard_cap_usd": "-3"},
                            "config_reflow_hard_cap_usd")
    assert recorder.config_reflow_hard_cap_usd == 0.0
