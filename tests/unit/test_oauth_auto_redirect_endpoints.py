# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Endpoint-resolution pins for the OAuth auto-redirect (#1411).

``auto_redirect_decision`` returns an endpoint *name* that ``cps/web.py`` feeds
straight to ``url_for()`` on the ``GET /login`` path. If a name in
``_PROVIDER_ENDPOINTS`` ever stops resolving — flask_dance renames its login
view, ``oauth_bb`` renames a provider, or the ``url_prefix`` moves — ``url_for``
raises ``BuildError`` and **every** login in OAuth-only mode 500s.

The companion suite (``test_oauth_auto_redirect.py``) exercises the decision
function against hand-built provider dicts, so it never touches Flask routing
and cannot catch that class of break. These tests register the real flask_dance
blueprints the way ``cps/oauth_bb.py`` does and assert the names resolve.
"""

import importlib.util
import re
from pathlib import Path

import flask
import pytest
from flask_dance.consumer import OAuth2ConsumerBlueprint
from flask_dance.contrib.github import make_github_blueprint
from flask_dance.contrib.google import make_google_blueprint

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "cps" / "oauth_auto_redirect.py"
_OAUTH_BB_PATH = _REPO_ROOT / "cps" / "oauth_bb.py"

_SPEC = importlib.util.spec_from_file_location("oauth_auto_redirect", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
oauth_auto_redirect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oauth_auto_redirect)

pytestmark = pytest.mark.unit

# Mirrors cps/oauth_bb.py: every blueprint is registered under this prefix.
_URL_PREFIX = "/login"


def _app_with_real_oauth_blueprints():
    """Register the same three blueprints ``generate_oauth_blueprints`` builds."""
    app = flask.Flask(__name__)
    app.register_blueprint(
        make_github_blueprint(client_id="cid", client_secret="secret"),
        url_prefix=_URL_PREFIX,
    )
    app.register_blueprint(
        make_google_blueprint(client_id="cid", client_secret="secret"),
        url_prefix=_URL_PREFIX,
    )
    app.register_blueprint(
        OAuth2ConsumerBlueprint(
            "generic",
            __name__,
            client_id="cid",
            client_secret="secret",
            base_url="https://idp.example.com",
            authorization_url="https://idp.example.com/authorize",
            token_url="https://idp.example.com/token",
        ),
        url_prefix=_URL_PREFIX,
    )
    return app


@pytest.mark.parametrize(
    "provider_name", sorted(oauth_auto_redirect._PROVIDER_ENDPOINTS)
)
def test_every_mapped_endpoint_resolves_with_url_for(provider_name):
    """A mapped endpoint must survive ``url_for`` — otherwise /login 500s."""
    endpoint = oauth_auto_redirect._PROVIDER_ENDPOINTS[provider_name]
    app = _app_with_real_oauth_blueprints()

    with app.test_request_context():
        resolved = flask.url_for(endpoint)

    assert resolved == f"{_URL_PREFIX}/{provider_name}"


def test_decision_output_is_always_a_resolvable_endpoint():
    """Drive the real decision function, then resolve whatever it hands back."""
    app = _app_with_real_oauth_blueprints()

    for provider_name in sorted(oauth_auto_redirect._PROVIDER_ENDPOINTS):
        endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
            {},
            [{"provider_name": provider_name, "active": True}],
            {},
        )

        assert next_url is None
        assert endpoint is not None
        with app.test_request_context():
            assert flask.url_for(endpoint).startswith(_URL_PREFIX)


def test_mapped_provider_names_exist_in_oauth_bb():
    """A typo'd key is dead code that silently disables auto-redirect.

    Deliberately one-directional: a provider present in ``oauth_bb`` but absent
    from the map degrades safely (no redirect, login page renders as before), so
    adding a fourth provider must not fail this test. A key that matches *no*
    real provider never fires and is always a mistake.
    """
    source = _OAUTH_BB_PATH.read_text(encoding="utf-8")
    known = set(re.findall(r"provider_name=['\"]([a-z_]+)['\"]", source))

    assert known, "failed to parse provider_name values out of cps/oauth_bb.py"

    unknown = sorted(set(oauth_auto_redirect._PROVIDER_ENDPOINTS) - known)
    assert not unknown, (
        f"_PROVIDER_ENDPOINTS maps provider(s) {unknown} that cps/oauth_bb.py "
        "never creates; auto-redirect can never fire for them"
    )
