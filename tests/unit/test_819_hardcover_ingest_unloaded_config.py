# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #819: Hardcover auto-fetch crashes at
ingest time with ``'ConfigSQL' object has no attribute 'config_hardcover_token'``.

Root cause: the #790 resolver methods ``ConfigSQL.resolved_hardcover_token()``
and ``ConfigSQL.hardcover_token_from_env()`` read ``self.config_hardcover_token``
directly. That column lives on the mapped ``_Settings`` class; the ``ConfigSQL``
*wrapper* only gains the instance attribute after ``load()`` runs. In the
ingest-processor subprocess the global ``config`` wrapper is not fully loaded,
so the attribute is absent and the unguarded access raises ``AttributeError`` —
exactly the symptom two reporters hit on v4.1.9.

Before #790 the sole consumer (the Hardcover provider's ``search``) read the
token with ``getattr(config, "config_hardcover_token", None)``, which returned
``None`` safely on an unloaded wrapper. #790 dropped that defensive access when
it centralised token resolution, regressing every ingest-time / subprocess /
CLI context where the wrapper is not loaded.

These tests build a wrapper the way the ingest subprocess sees it — a plain
``ConfigSQL()`` whose ``config_hardcover_token`` attribute was never set — and
assert the resolvers degrade gracefully (env fallback still works, no crash)
instead of raising. They fail on pre-fix code with ``AttributeError``.

Note the existing ``test_743_hardcover_env_token.py`` masks this bug: its
``_bare_config()`` helper explicitly does ``cfg.config_hardcover_token = None``,
which the real ingest path never does.
"""

from __future__ import annotations

import pytest


def _unloaded_config():
    """A ConfigSQL wrapper as the ingest subprocess sees it: constructed but
    never ``load()``-ed, so ``config_hardcover_token`` is genuinely absent."""
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    # Deliberately do NOT set config_hardcover_token — mirror the unloaded
    # wrapper. Guard the test's own premise: the attribute must be missing.
    assert not hasattr(cfg, "config_hardcover_token")
    return cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.delenv("HARDCOVER_TOKEN_FILE", raising=False)


def test_resolved_token_no_crash_on_unloaded_config():
    """The reporter's exact crash path: resolver must not raise when the
    wrapper has no config_hardcover_token attribute (no token anywhere)."""
    cfg = _unloaded_config()
    assert cfg.resolved_hardcover_token() == ""


def test_resolved_token_env_fallback_on_unloaded_config(monkeypatch):
    """Reporter #819's setup: token in the environment, config unloaded at
    ingest — the env token must still resolve (and Bearer-trim) rather than
    the whole fetch aborting with AttributeError."""
    cfg = _unloaded_config()
    monkeypatch.setenv("HARDCOVER_TOKEN", " Bearer ingest-env-tok ")
    assert cfg.resolved_hardcover_token() == "ingest-env-tok"


def test_token_file_fallback_on_unloaded_config(monkeypatch, tmp_path):
    """Docker-secrets HARDCOVER_TOKEN_FILE must also survive an unloaded wrapper."""
    cfg = _unloaded_config()
    secret = tmp_path / "hardcover_token"
    secret.write_text("file-tok\n", encoding="utf-8")
    monkeypatch.setenv("HARDCOVER_TOKEN_FILE", str(secret))
    assert cfg.resolved_hardcover_token() == "file-tok"


def test_token_from_env_no_crash_on_unloaded_config(monkeypatch):
    """hardcover_token_from_env() (admin-form hint) shares the unguarded access
    and must also degrade gracefully rather than raise."""
    cfg = _unloaded_config()
    assert cfg.hardcover_token_from_env() is False
    monkeypatch.setenv("HARDCOVER_TOKEN", "env-tok")
    assert cfg.hardcover_token_from_env() is True


def test_resolvers_use_getattr_not_raw_attr():
    """Source-pin: both resolvers must access the column defensively so a
    future edit can't reintroduce the unguarded ``self.config_hardcover_token``
    that broke ingest. Pins the fix, not trivia."""
    import inspect

    from cps.config_sql import ConfigSQL

    for name in ("resolved_hardcover_token", "hardcover_token_from_env"):
        src = inspect.getsource(getattr(ConfigSQL, name))
        assert "self.config_hardcover_token" not in src, (
            f"{name} reads self.config_hardcover_token directly; use "
            f'getattr(self, "config_hardcover_token", None) so an unloaded '
            f"config (ingest subprocess) does not raise — fork #819"
        )
        assert 'getattr(self, "config_hardcover_token"' in src.replace("'", '"'), (
            f"{name} must resolve the token via getattr with a default"
        )
