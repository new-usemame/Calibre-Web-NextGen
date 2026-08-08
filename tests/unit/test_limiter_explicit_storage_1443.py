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

whenever ``Limiter`` is constructed without ``storage_uri``. It fires on the
*absence of an explicit choice*, not on the storage being wrong: this app
serves from a single process (gevent ``WSGIServer`` or a tornado ``IOLoop``,
never a pre-fork pool), so in-memory counters are shared by every request
handler that reads them and are the correct backend here.

The fix is to say so. These tests pin that the declaration stays.

The construction test deliberately does not hardcode the kwargs — it reads
the real ``Limiter(...)`` call out of ``cps/__init__.py`` and instantiates
flask_limiter with exactly those arguments, so it exercises the same code
path that warned in the reporter's log rather than a hand-copied imitation
that could drift away from the source it is meant to protect.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CPS_INIT = REPO_ROOT / "cps" / "__init__.py"

WARNING_FRAGMENT = "no storage was explicitly specified"


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
    def test_limiter_is_constructed_with_an_explicit_storage_uri(self):
        """The kwarg must be present. Its absence is the entire bug."""
        kwargs = {kw.arg for kw in _limiter_call().keywords if kw.arg}
        assert "storage_uri" in kwargs, (
            "cps/__init__.py constructs Limiter() without storage_uri, so "
            "flask_limiter falls back to its implicit in-memory default and "
            "warns on every startup (#1443). The backend is correct for this "
            "single-process server; it just has to be stated."
        )

    def test_binding_to_an_app_with_the_real_kwargs_emits_no_storage_warning(self):
        """Behavioural pin: build a Limiter with the app's own arguments,
        bind it to a Flask app, and assert flask_limiter stays quiet.

        ``init_app`` is where the warning is raised (verified against
        flask_limiter 3.12 — construction alone is silent), so the binding
        step is the part that reproduces the reporter's log. Red before the
        fix, green after.
        """
        flask_limiter = pytest.importorskip("flask_limiter")
        flask = pytest.importorskip("flask")

        call = _limiter_call()
        kwargs = {}
        for kw in call.keywords:
            if kw.arg is None:
                continue
            try:
                kwargs[kw.arg] = ast.literal_eval(kw.value)
            except ValueError:
                # A non-literal argument (e.g. a function reference) can't be
                # rebuilt here; flask_limiter does not inspect it while
                # deciding whether to warn, so a placeholder is faithful.
                kwargs[kw.arg] = True

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            limiter = flask_limiter.Limiter(**kwargs)
            limiter.init_app(flask.Flask(__name__))

        offenders = [
            str(w.message) for w in caught if WARNING_FRAGMENT in str(w.message)
        ]
        assert not offenders, (
            "flask_limiter warned about implicit in-memory storage while "
            "being bound to an app with the app's own kwargs — this is the "
            f"startup noise from #1443. Warnings: {offenders}"
        )

    def test_storage_uri_is_a_local_backend_not_a_network_service(self):
        """Guard the fix against becoming a rule-6 violation.

        Pointing this at redis:// or memcached:// would add an external
        service dependency that the image does not ship a client for, turning
        a log warning into a hard startup failure for every existing user.
        """
        for kw in _limiter_call().keywords:
            if kw.arg == "storage_uri":
                value = ast.literal_eval(kw.value)
                assert value.startswith("memory://"), (
                    f"storage_uri is {value!r}. This server runs one process "
                    f"and ships no redis/memcached client, so a network "
                    f"backend would break startup rather than fix a warning."
                )
                return
        pytest.fail("storage_uri kwarg missing — see the first test in this class.")
