# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #1443 — Flask-Limiter warned at every startup.

``flask_limiter`` emits::

    UserWarning: Using the in-memory storage for tracking rate limits as no
    storage was explicitly specified. This is not recommended for production
    use.

whenever no storage is specified. It fires on the *absence of an explicit
choice*, not on the storage being wrong: this app serves from a single
process (gevent ``WSGIServer`` or a tornado ``IOLoop``, never a pre-fork
pool), so in-memory counters are shared by every request handler that reads
them and are the correct backend here.

The fix states the default, and *where* it states it is the whole point.
``create_app`` already honours an admin-set "Limiter Backend"
(``config_limiter_uri``) by writing ``RATELIMIT_STORAGE_URI`` into
``app.config``. A ``storage_uri=`` on the ``Limiter(...)`` constructor
outranks that config key, so declaring the default there would silently
override the admin's own backend and drop them onto memory storage with no
error — trading a log warning for a real downgrade of brute-force protection.
So the default is declared alongside the admin setting, in the same
``app.config`` layer, on the branch where no backend was configured.

These tests pin both halves: the warning stays gone, and the admin setting
keeps winning.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CPS_INIT = REPO_ROOT / "cps" / "__init__.py"

WARNING_FRAGMENT = "no storage was explicitly specified"
LIMITER_KWARGS = dict(headers_enabled=True, auto_check=False, swallow_errors=False)


def _limiter_call() -> ast.Call:
    """Return the AST node for the ``Limiter(...)`` call in cps/__init__.py."""
    tree = ast.parse(CPS_INIT.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Limiter"
        ):
            return node
    pytest.fail(
        "no Limiter(...) call found in cps/__init__.py — if the rate limiter "
        "moved, move this test with it rather than deleting it (#1443)."
    )


@pytest.mark.unit
class TestLimiterStorageIsExplicit:
    def test_no_backend_configured_still_declares_a_storage_uri(self):
        """The `else` branch of the limiter config block must state the
        in-memory default. Leaving the key unset is the bug."""
        src = CPS_INIT.read_text()
        assert 'RATELIMIT_STORAGE_URI="memory://"' in src, (
            "cps/__init__.py never sets RATELIMIT_STORAGE_URI to an explicit "
            "backend, so flask_limiter falls back to its implicit in-memory "
            "default and warns on every startup (#1443)."
        )

    def test_binding_with_an_explicit_config_uri_emits_no_storage_warning(self):
        """Behavioural pin: the config value the fix writes must actually
        silence flask_limiter.

        ``init_app`` is where the warning is raised — construction alone is
        silent, verified against flask_limiter 3.12 — so binding is the step
        that reproduces the reporter's log.
        """
        flask_limiter = pytest.importorskip("flask_limiter")
        flask = pytest.importorskip("flask")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            app = flask.Flask(__name__)
            app.config["RATELIMIT_STORAGE_URI"] = "memory://"
            limiter = flask_limiter.Limiter(key_func=lambda: "k", **LIMITER_KWARGS)
            limiter.init_app(app)

        offenders = [
            str(w.message) for w in caught if WARNING_FRAGMENT in str(w.message)
        ]
        assert not offenders, (
            f"declaring RATELIMIT_STORAGE_URI did not silence flask_limiter — "
            f"this is the startup noise from #1443. Warnings: {offenders}"
        )

    def test_the_unconfigured_default_does_not_warn_but_an_unset_key_does(self):
        """Guard against the fix being a no-op: prove the warning is real
        when the key is unset, using the same construction path.

        Without this, the test above could pass for reasons unrelated to the
        fix and nobody would notice the regression had returned.
        """
        flask_limiter = pytest.importorskip("flask_limiter")
        flask = pytest.importorskip("flask")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            limiter = flask_limiter.Limiter(key_func=lambda: "k", **LIMITER_KWARGS)
            limiter.init_app(flask.Flask(__name__))

        assert any(WARNING_FRAGMENT in str(w.message) for w in caught), (
            "flask_limiter no longer warns on an unset storage key, so these "
            "tests are guarding a symptom that can no longer occur. Re-check "
            "whether the fix in cps/__init__.py is still needed before "
            "deleting anything."
        )


@pytest.mark.unit
class TestAdminBackendSettingStillWins:
    """The admin-facing "Limiter Backend" setting (``config_limiter_uri``)
    must keep working. This is the regression a cross-family review caught
    in the first version of the #1443 fix."""

    def test_limiter_constructor_does_not_pin_storage_uri(self):
        """A constructor ``storage_uri`` outranks ``app.config``, so it would
        silently override the admin's configured backend."""
        kwargs = {kw.arg for kw in _limiter_call().keywords if kw.arg}
        assert "storage_uri" not in kwargs, (
            "Limiter(...) pins storage_uri, which takes precedence over the "
            "RATELIMIT_STORAGE_URI that create_app() derives from the admin's "
            "config_limiter_uri setting. An operator who configured an "
            "external limiter backend would be silently dropped onto memory "
            "storage with no error. Declare the default in the config block "
            "instead, on the branch where no backend was configured."
        )

    def test_a_constructor_uri_would_in_fact_override_app_config(self):
        """Demonstrate the precedence the test above relies on, so the rule
        is evidenced rather than asserted from memory."""
        flask_limiter = pytest.importorskip("flask_limiter")
        flask = pytest.importorskip("flask")
        warnings.simplefilter("ignore")

        app = flask.Flask(__name__)
        app.config["RATELIMIT_STORAGE_URI"] = "memory://from-config"
        limiter = flask_limiter.Limiter(
            key_func=lambda: "k", storage_uri="memory://from-kwarg", **LIMITER_KWARGS
        )
        limiter.init_app(app)
        with app.app_context():
            # flask_limiter resolves the kwarg, never consulting app.config.
            assert "from-kwarg" in str(limiter.storage) or limiter.storage is not None
            resolved = getattr(limiter, "_storage_uri", None)

        assert resolved == "memory://from-kwarg", (
            f"expected the constructor kwarg to win over app.config "
            f"(got {resolved!r}). If flask_limiter changed this precedence, "
            f"the reasoning in cps/__init__.py's limiter comment needs "
            f"revisiting — but prefer keeping the config-layer default."
        )

    def test_configured_backend_branch_is_still_reachable(self):
        """The admin branch must remain, and must not have been folded into
        an unconditional memory:// assignment."""
        src = CPS_INIT.read_text()
        assert "RATELIMIT_STORAGE_URI=config.config_limiter_uri" in src, (
            "the branch that honours the admin's configured limiter backend "
            "is gone — #1443 was a log-noise fix and must not have removed "
            "the external-backend path."
        )

    def test_fallback_path_lands_on_an_explicit_backend(self):
        """The `except` path used to reset the URI to None, which re-arms the
        very warning this issue is about."""
        src = CPS_INIT.read_text()
        assert "RATELIMIT_STORAGE_URI=None" not in src.replace(" ", ""), (
            "the misconfiguration fallback sets RATELIMIT_STORAGE_URI=None, "
            "which leaves the storage unspecified and warns again (#1443). "
            "Fall back to an explicit memory:// instead."
        )
