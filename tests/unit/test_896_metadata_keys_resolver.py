# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #896: the Fetch Metadata modal's 🔑 Keys
panel showed "Not configured" for Hardcover even when a working token was
supplied via ``HARDCOVER_TOKEN`` / ``HARDCOVER_TOKEN_FILE``.

Issue #743 routed the *zero-result classifier* in this same file through
``config.resolved_hardcover_token()``, but ``metadata_keys()`` kept asking
the raw config column. An env/file token never lands in that column (by
design — see ``resolved_hardcover_token``), so the badge read False while
the provider itself worked. Same bug in ``metadata_keys_save()``, which
derived its answer from the posted value alone.

The fix gives the provider registry an optional ``resolver`` hook and makes
``_provider_configured()`` the single source of truth for both endpoints.

Note the two #743 source-pins do NOT catch this: the raw read went through
registry indirection (``spec["config"]``) rather than the literal column
name, and the file-level "uses the resolver" grep was satisfied by the
classifier alone. These tests are behavioural for that reason — they call
the real view function and assert on the JSON the front-ends consume.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask


def _bare_config():
    """A real ConfigSQL with both key columns empty (no DB required)."""
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.config_hardcover_token = None
    cfg.config_google_books_api_key = None
    return cfg


def _keys_payload(monkeypatch, cfg):
    """Call the real /metadata/keys view and return its decoded JSON.

    ``__wrapped__`` steps past @user_login_required (functools.wraps); the
    route is exercised exactly as the modal hits it.
    """
    import cps.search_metadata as sm

    monkeypatch.setattr(sm, "config", cfg)
    app = Flask(__name__)
    with app.test_request_context("/metadata/keys"):
        resp = sm.metadata_keys.__wrapped__()
    return json.loads(resp.get_data(as_text=True))


def _entry(payload, pid):
    entry = next((e for e in payload if e["id"] == pid), None)
    assert entry is not None, f"provider {pid!r} missing from payload: {payload}"
    return entry


# --- the reported symptom -------------------------------------------------

def test_env_token_reports_configured(monkeypatch):
    """HARDCOVER_TOKEN set, DB column empty -> badge must say Configured."""
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token-value")
    monkeypatch.delenv("HARDCOVER_TOKEN_FILE", raising=False)

    payload = _keys_payload(monkeypatch, _bare_config())

    assert _entry(payload, "hardcover")["configured"] is True, (
        "An HARDCOVER_TOKEN env token is what the provider actually uses, so "
        "the Keys panel must not report it as missing (issue #896)."
    )


def test_token_file_reports_configured(monkeypatch, tmp_path):
    """HARDCOVER_TOKEN_FILE (docker-secrets style) counts as configured."""
    secret = tmp_path / "hardcover_token"
    secret.write_text("file-token-value\n", encoding="utf-8")
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(secret))

    payload = _keys_payload(monkeypatch, _bare_config())

    assert _entry(payload, "hardcover")["configured"] is True, (
        "HARDCOVER_TOKEN_FILE resolves to a working token, so the Keys panel "
        "must report it as configured (issue #896)."
    )


def test_db_token_still_reports_configured(monkeypatch):
    """The admin-configured column keeps working (no regression on #790)."""
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.delenv("HARDCOVER_TOKEN_FILE", raising=False)
    cfg = _bare_config()
    cfg.config_hardcover_token = "db-token-value"

    payload = _keys_payload(monkeypatch, cfg)

    assert _entry(payload, "hardcover")["configured"] is True


# --- the negative case ----------------------------------------------------

def test_no_token_anywhere_reports_not_configured(monkeypatch):
    """No source at all -> False, so the 'go get a key' hint still shows."""
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.delenv("HARDCOVER_TOKEN_FILE", raising=False)

    payload = _keys_payload(monkeypatch, _bare_config())

    assert _entry(payload, "hardcover")["configured"] is False


# --- blast radius: providers without a resolver are untouched -------------

def test_provider_without_resolver_uses_its_column(monkeypatch):
    """Google Books has no env fallback; it must still read its column."""
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    cfg = _bare_config()

    payload = _keys_payload(monkeypatch, cfg)
    assert _entry(payload, "google")["configured"] is False

    cfg.config_google_books_api_key = "gb-key"
    payload = _keys_payload(monkeypatch, cfg)
    assert _entry(payload, "google")["configured"] is True


# --- the consumer contract both front-ends read --------------------------

def test_payload_shape_is_unchanged(monkeypatch):
    """CoverPicker.tsx (ProviderKey) and get_meta.js pin these keys."""
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token-value")
    payload = _keys_payload(monkeypatch, _bare_config())

    entry = _entry(payload, "hardcover")
    for field in ("id", "name", "configured", "signup", "help", "can_edit"):
        assert field in entry, f"{field} is part of the /metadata/keys contract"
    assert isinstance(entry["configured"], bool), (
        "configured must stay a plain bool — frontend/src/lib/coverPicker.ts "
        "types it as boolean and get_meta.js branches on it directly."
    )


def test_payload_never_leaks_the_token_value(monkeypatch):
    """Presence only, never the secret itself."""
    monkeypatch.setenv("HARDCOVER_TOKEN", "super-secret-token")
    payload = _keys_payload(monkeypatch, _bare_config())

    assert "super-secret-token" not in json.dumps(payload), (
        "/metadata/keys must report only whether a key exists, never its value."
    )


# --- the same defect on the save path ------------------------------------

def test_save_clearing_db_field_reports_env_token_still_active(monkeypatch):
    """Clearing the admin field while an env token is live must not claim
    the provider is now unconfigured — the env token still works."""
    import cps.search_metadata as sm

    monkeypatch.setenv("HARDCOVER_TOKEN", "env-token-value")
    cfg = _bare_config()
    cfg.config_hardcover_token = "db-token-value"
    monkeypatch.setattr(cfg, "save", lambda: None)
    monkeypatch.setattr(sm, "config", cfg)
    monkeypatch.setattr(sm, "_is_admin_user", lambda: True)

    app = Flask(__name__)
    with app.test_request_context(
        "/metadata/keys/hardcover", method="POST", json={"value": ""}
    ):
        resp = sm.metadata_keys_save.__wrapped__("hardcover")
    body = json.loads(resp.get_data(as_text=True))

    assert body["configured"] is True, (
        "The POST response drives the badge immediately (CoverPicker.tsx "
        "setConfigured(r.configured)); with an env token still resolving it "
        "must not flip to Not configured until a refetch corrects it (#896)."
    )


# --- guard: the registry must keep routing Hardcover through the resolver -

def test_registry_declares_the_hardcover_resolver():
    import cps.search_metadata as sm

    assert sm.PROVIDER_KEY_REGISTRY["hardcover"].get("resolver") == (
        "resolved_hardcover_token"
    ), (
        "Hardcover's key can come from the DB, HARDCOVER_TOKEN or "
        "HARDCOVER_TOKEN_FILE; the Keys panel must ask the resolver, not the "
        "raw column (issue #896)."
    )


def test_configured_helper_is_the_single_source_of_truth():
    """Both endpoints must go through one helper, so a future provider with
    an env fallback cannot regress one path and fix the other (#896)."""
    import ast
    import inspect

    import cps.search_metadata as sm

    for fn in (sm.metadata_keys, sm.metadata_keys_save):
        raw = getattr(fn, "__wrapped__", fn)
        tree = ast.parse(inspect.getsource(raw).lstrip())
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_provider_configured" in called, (
            f"{raw.__name__} must derive 'configured' from "
            "_provider_configured() rather than reading a column directly."
        )
