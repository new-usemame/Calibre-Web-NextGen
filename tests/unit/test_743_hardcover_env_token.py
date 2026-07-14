# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #743: the documented ``HARDCOVER_TOKEN``
environment variable only worked in *some* code paths.

The env var was bolted on as scattered ``... or getenv("HARDCOVER_TOKEN")``
fallbacks. Consumers that read ``config.config_hardcover_token`` directly
never saw it — most visibly the Fetch Metadata modal's zero-result
classifier (``search_metadata.py``), which told users with a working env
token to "set a Hardcover API key", and the admin form, which offered no
hint that an environment token was active.

The fix routes every global-token consumer through one resolver,
``ConfigSQL.resolved_hardcover_token()`` (DB value → HARDCOVER_TOKEN →
HARDCOVER_TOKEN_FILE, "Bearer "-trimmed), adds the docker-secrets-style
``HARDCOVER_TOKEN_FILE`` variant the reporter asked for, and surfaces an
"env token is active" note on the admin form.

The behavioural tests fail on pre-fix code (no resolver existed); the
source-pins fail if a consumer regresses to reading the raw column.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bare_config():
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.config_hardcover_token = None
    return cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.delenv("HARDCOVER_TOKEN_FILE", raising=False)


def test_env_token_resolves_and_trims_bearer(monkeypatch):
    cfg = _bare_config()
    monkeypatch.setenv("HARDCOVER_TOKEN", " Bearer env-tok ")
    assert cfg.resolved_hardcover_token() == "env-tok"
    assert cfg.hardcover_token_from_env() is True


def test_db_value_wins_over_env(monkeypatch):
    cfg = _bare_config()
    cfg.config_hardcover_token = "db-tok"
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-tok")
    assert cfg.resolved_hardcover_token() == "db-tok"
    assert cfg.hardcover_token_from_env() is False


def test_token_file_fallback(monkeypatch, tmp_path):
    cfg = _bare_config()
    secret = tmp_path / "hardcover_token"
    secret.write_text("file-tok\n", encoding="utf-8")
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(secret))
    assert cfg.resolved_hardcover_token() == "file-tok"
    assert cfg.hardcover_token_from_env() is True


def test_missing_token_file_degrades_to_no_token(monkeypatch, tmp_path):
    cfg = _bare_config()
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(tmp_path / "nope"))
    assert cfg.resolved_hardcover_token() == ""
    assert cfg.hardcover_token_from_env() is False


def test_no_sources_means_empty():
    cfg = _bare_config()
    assert cfg.resolved_hardcover_token() == ""
    assert cfg.hardcover_token_from_env() is False


# --- source-pins: consumers must go through the resolver -------------------

_CONSUMERS = [
    ("cps/search_metadata.py", "the Fetch Metadata zero-result classifier"),
    ("cps/admin.py", "the manual Hardcover trigger"),
    ("cps/schedule.py", "the auto-fetch scheduler gate"),
    ("cps/cwa_functions.py", "the CWA settings page gate"),
    ("cps/tasks/auto_hardcover_id.py", "the auto-hardcover-id task"),
    ("cps/metadata_provider/hardcover.py", "the metadata provider"),
]


@pytest.mark.parametrize("rel,what", _CONSUMERS)
def test_consumer_uses_resolver(rel, what):
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "resolved_hardcover_token()" in src, (
        f"{rel} ({what}) must resolve the global Hardcover token through "
        "config.resolved_hardcover_token() so DB, HARDCOVER_TOKEN and "
        "HARDCOVER_TOKEN_FILE behave identically everywhere (issue #743)."
    )


def test_classifier_does_not_read_raw_column():
    src = (REPO_ROOT / "cps/search_metadata.py").read_text(encoding="utf-8")
    assert 'getattr(config, "config_hardcover_token"' not in src, (
        "search_metadata.py must not read the raw config column — that is "
        "exactly the path that ignored env tokens and produced the bogus "
        "'set a Hardcover API key' hint (issue #743)."
    )


def test_hardcover_key_presence_is_never_read_from_the_column():
    """The pin above only catches a *literal* column read. #896 shipped
    anyway because ``metadata_keys()`` reached the column through registry
    indirection (``getattr(config, spec["config"])``). Any provider whose
    key can come from env/file must declare a resolver, so presence is
    asked of the resolver no matter how the column name is spelled."""
    from cps.search_metadata import PROVIDER_KEY_REGISTRY

    assert PROVIDER_KEY_REGISTRY["hardcover"].get("resolver") == (
        "resolved_hardcover_token"
    ), (
        "Hardcover resolves its token from the DB, HARDCOVER_TOKEN or "
        "HARDCOVER_TOKEN_FILE — every consumer that reports whether a key "
        "exists must go through that resolver (issues #743, #896)."
    )


def test_admin_form_mentions_env_token():
    tpl = (REPO_ROOT / "cps/templates/config_edit.html").read_text(encoding="utf-8")
    assert "hardcover_token_from_env()" in tpl, (
        "config_edit.html must tell admins when an environment token is "
        "active — the silent empty field is what got #743 reported."
    )
