# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #1242: ComicVine's API key was a source literal
with no way for an install to supply its own.

Reported by @tomaioo, whose patch swapped the literal for
``os.environ.get("COMICVINE_API_KEY", "")`` and skipped the search when it was
empty. The concern is real — every install sends one shared key, so any install
can be rate-limited by the others — but that shape had three defects these
tests pin against:

1. It removed the zero-configuration default, so every existing install lost
   ComicVine search until it set a brand-new environment variable
   (``test_no_configured_key_falls_back_to_the_shared_key``,
   ``test_search_still_requests_when_no_key_is_configured``).
2. ``BASE_URL`` interpolates the key in the *class body*, so a key supplied
   after import — which is what the admin Keys panel does — never reached the
   request (``test_admin_key_set_after_import_reaches_the_request``,
   ``test_no_class_attribute_bakes_a_key_into_a_url``).
3. It bypassed ``PROVIDER_KEY_REGISTRY``, the subsystem this fork already has
   for provider keys, leaving no in-app way to set one
   (``TestRegistryIntegration``).

The adopted shape keeps the shared key as the default, resolves the install's
own key per request from the DB column / ``COMICVINE_API_KEY`` /
``COMICVINE_API_KEY_FILE``, and registers ComicVine in the registry so the 🔑
Keys panel can set it. It also reads ComicVine's response envelope: the API
reports an exhausted rate limit in a *200* body, so before this the failure the
whole issue is about was indistinguishable from "no matches"
(``TestErrorState``).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SHARED_KEY = "57558043c53943d5d1e96a9ad425b0eb85532ee6"


# --------------------------------------------------------------------------
# resolver — cps/config_sql.py::ConfigSQL.resolved_comicvine_api_key
# --------------------------------------------------------------------------

def _bare_config():
    from cps.config_sql import ConfigSQL

    cfg = ConfigSQL()
    cfg.config_comicvine_api_key = None
    return cfg


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("COMICVINE_API_KEY", raising=False)
    monkeypatch.delenv("COMICVINE_API_KEY_FILE", raising=False)


@pytest.mark.unit
class TestResolver:
    def test_no_key_anywhere_resolves_empty(self):
        assert _bare_config().resolved_comicvine_api_key() == ""

    def test_db_value_wins_over_env(self, monkeypatch):
        cfg = _bare_config()
        cfg.config_comicvine_api_key = "db-key"
        monkeypatch.setenv("COMICVINE_API_KEY", "env-key")
        assert cfg.resolved_comicvine_api_key() == "db-key"

    def test_env_resolves_when_db_is_empty(self, monkeypatch):
        monkeypatch.setenv("COMICVINE_API_KEY", "env-key")
        assert _bare_config().resolved_comicvine_api_key() == "env-key"

    def test_env_wins_over_file(self, monkeypatch, tmp_path):
        secret = tmp_path / "cv"
        secret.write_text("file-key\n", encoding="utf-8")
        monkeypatch.setenv("COMICVINE_API_KEY", "env-key")
        monkeypatch.setenv("COMICVINE_API_KEY_FILE", str(secret))
        assert _bare_config().resolved_comicvine_api_key() == "env-key"

    def test_secret_file_resolves_and_strips_trailing_newline(
        self, monkeypatch, tmp_path
    ):
        """A docker-secrets mount ends in a newline. Left on, it produces an
        unusable URL — the LOW finding on the original patch, which neither
        stripped nor quoted the value."""
        secret = tmp_path / "cv"
        secret.write_text("file-key\n", encoding="utf-8")
        monkeypatch.setenv("COMICVINE_API_KEY_FILE", str(secret))
        assert _bare_config().resolved_comicvine_api_key() == "file-key"

    def test_whitespace_only_values_are_not_a_key(self, monkeypatch):
        cfg = _bare_config()
        cfg.config_comicvine_api_key = "   "
        monkeypatch.setenv("COMICVINE_API_KEY", "\t\n")
        assert cfg.resolved_comicvine_api_key() == ""

    def test_missing_secret_file_degrades_to_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COMICVINE_API_KEY_FILE", str(tmp_path / "nope"))
        assert _bare_config().resolved_comicvine_api_key() == ""

    def test_unloaded_config_wrapper_does_not_raise(self, monkeypatch):
        """The ingest subprocess runs an unloaded wrapper with no mapped
        column attribute; the env fallback still has to resolve rather than
        raising AttributeError (the #819 failure mode)."""
        from cps.config_sql import ConfigSQL

        cfg = ConfigSQL()  # deliberately not load()ed, no column attribute
        monkeypatch.setenv("COMICVINE_API_KEY", "env-key")
        assert cfg.resolved_comicvine_api_key() == "env-key"


# --------------------------------------------------------------------------
# registry — the Keys panel is generated from PROVIDER_KEY_REGISTRY
# --------------------------------------------------------------------------

@pytest.mark.unit
class TestRegistryIntegration:
    def test_comicvine_is_registered(self):
        import cps.search_metadata as sm

        assert "comicvine" in sm.PROVIDER_KEY_REGISTRY, (
            "Without a registry entry the Keys panel offers no way to set a "
            "ComicVine key, so the only remedy is editing compose and "
            "restarting (#1242)."
        )

    def test_registry_declares_the_resolver_not_the_raw_column(self):
        import cps.search_metadata as sm

        assert sm.PROVIDER_KEY_REGISTRY["comicvine"].get("resolver") == (
            "resolved_comicvine_api_key"
        ), (
            "COMICVINE_API_KEY / COMICVINE_API_KEY_FILE are never persisted "
            "to the column, so the panel badge must ask the resolver or it "
            "under-reports an env-supplied key (same shape as #896)."
        )

    def test_declared_resolver_and_column_actually_exist(self):
        """Both names are strings looked up by getattr at runtime, so a typo
        fails silently as 'not configured' forever."""
        import cps.search_metadata as sm
        from cps.config_sql import ConfigSQL, _Settings

        spec = sm.PROVIDER_KEY_REGISTRY["comicvine"]
        assert callable(getattr(ConfigSQL, spec["resolver"], None))
        assert hasattr(_Settings, spec["config"])

    def test_configured_badge_is_false_on_a_stock_install(self, monkeypatch):
        """The badge answers "do you have your OWN key", so the shared
        fallback must not make it read as configured."""
        import cps.search_metadata as sm

        spec = sm.PROVIDER_KEY_REGISTRY["comicvine"]
        cfg = _bare_config()
        monkeypatch.setattr(sm, "config", cfg)

        assert sm._provider_configured(spec) is False
        cfg.config_comicvine_api_key = "own-key"
        assert sm._provider_configured(spec) is True

    def test_empty_result_hint_points_at_the_shared_quota(self, monkeypatch):
        """A consistently-empty ComicVine on a stock install is usually the
        shared rate limit, not a genuine no-match. Mirrors Google's branch."""
        import cps.search_metadata as sm

        provider = types.SimpleNamespace(__id__="comicvine")
        cfg = _bare_config()
        monkeypatch.setattr(sm, "config", cfg)

        status, message = sm._classify_empty_provider(provider)
        assert status == "empty"
        assert "Keys panel" in message and "shared key" in message

        cfg.config_comicvine_api_key = "own-key"
        _, own_key_message = sm._classify_empty_provider(provider)
        assert own_key_message == "No results for this query", (
            "An install with its own key must not be told to go get one."
        )


# --------------------------------------------------------------------------
# provider — cps/metadata_provider/comicvine.py
# --------------------------------------------------------------------------

def _stub_cps_modules():
    """Enough of the cps namespace for comicvine.py to import.

    The real package init bootstraps login + databases; the provider only
    needs logger, config and the Metadata base. Other tests in this directory
    stub the same namespace, so top up any MISSING attribute and never replace
    one that is already there: this module is imported at collection time and
    the rest of the suite shares the interpreter, so clobbering a real
    ``cps.config`` here fails unrelated tests hundreds of files later. Every
    test below swaps the key source through monkeypatch instead, which reverts.
    """
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

    logger_mod = sys.modules.get("cps.logger") or types.ModuleType("cps.logger")
    if not hasattr(logger_mod, "create"):
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
            info=lambda *_a, **_k: None,
            error=lambda *_a, **_k: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    if not hasattr(cps_pkg, "config"):
        cps_pkg.config = types.SimpleNamespace(
            resolved_comicvine_api_key=lambda: ""
        )

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    if "cps.services.Metadata" not in sys.modules:
        path = REPO_ROOT / "cps" / "services" / "Metadata.py"
        spec = importlib.util.spec_from_file_location("cps.services.Metadata", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cps.services.Metadata"] = module
        spec.loader.exec_module(module)

    if "cps.metadata_provider" not in sys.modules:
        pkg = types.ModuleType("cps.metadata_provider")
        pkg.__path__ = [str(REPO_ROOT / "cps" / "metadata_provider")]
        sys.modules["cps.metadata_provider"] = pkg

    return cps_pkg


def _load_comicvine():
    _stub_cps_modules()
    path = REPO_ROOT / "cps" / "metadata_provider" / "comicvine.py"
    spec = importlib.util.spec_from_file_location(
        "cps.metadata_provider.comicvine", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.metadata_provider.comicvine"] = module
    spec.loader.exec_module(module)
    return module


comicvine = _load_comicvine()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _ok_payload():
    """A real-shaped ComicVine /search envelope with one issue."""
    return {
        "error": "OK",
        "status_code": 1,
        "number_of_total_results": 1,
        "results": [
            {
                "id": 12345,
                "name": "The Killing Joke",
                "issue_number": "1",
                "volume": {"name": "Batman"},
                "site_detail_url": "https://comicvine.gamespot.com/x/4000-12345/",
                "image": {"original_url": "https://comicvine.example/cover.jpg"},
                "store_date": "1988-03-01",
                "description": "<p>A one-shot.</p>",
            }
        ],
    }


def _capture_request(monkeypatch, payload=None):
    """Point the provider's requests.get at a recorder. Returns the box the
    request URL lands in."""
    box = {}

    def fake_get(url, headers=None, timeout=None):
        box["url"] = url
        box["headers"] = headers
        return _FakeResponse(payload if payload is not None else _ok_payload())

    monkeypatch.setattr(comicvine.requests, "get", fake_get)
    return box


def _set_resolved_key(monkeypatch, value):
    """Swap what the provider's config resolver returns, as the admin Keys
    panel effectively does at runtime.

    Replaces the module-local ``config`` reference rather than setting an
    attribute on the shared config object, so nothing leaks into other tests
    (and so this works whether cps.config is the real ConfigSQL or a stub).
    """
    monkeypatch.setattr(
        comicvine,
        "config",
        types.SimpleNamespace(resolved_comicvine_api_key=lambda: value),
    )


def _provider():
    p = comicvine.ComicVine()
    p.active = True
    return p


@pytest.mark.unit
class TestKeyResolutionReachesTheRequest:
    def test_no_configured_key_falls_back_to_the_shared_key(self, monkeypatch):
        """A stock install must keep working. The reported patch made this
        install search nothing at all."""
        _set_resolved_key(monkeypatch, "")
        box = _capture_request(monkeypatch)

        _provider().search("Batman")

        assert f"api_key={SHARED_KEY}" in box["url"]

    def test_search_still_requests_when_no_key_is_configured(self, monkeypatch):
        _set_resolved_key(monkeypatch, "")
        box = _capture_request(monkeypatch)

        results = _provider().search("Batman")

        assert box.get("url"), (
            "No request was issued at all — this is exactly the regression "
            "the reported patch introduced for every install without the new "
            "environment variable (#1242)."
        )
        assert len(results) == 1

    def test_admin_key_set_after_import_reaches_the_request(self, monkeypatch):
        """The Keys panel writes the key long after this module imported. A
        key interpolated into a class attribute at import time can never be
        seen — the core defect in the reported patch."""
        _set_resolved_key(monkeypatch, "admins-own-key")
        box = _capture_request(monkeypatch)

        _provider().search("Batman")

        assert "api_key=admins-own-key" in box["url"]
        assert SHARED_KEY not in box["url"], (
            "The shared key leaked into a request from an install that "
            "configured its own."
        )

    def test_key_is_url_quoted(self, monkeypatch):
        """A pasted key can carry characters that break a URL; the original
        patch interpolated the raw value."""
        _set_resolved_key(monkeypatch, "key with space")
        box = _capture_request(monkeypatch)

        _provider().search("Batman")

        assert "key%20with%20space" in box["url"]

    def test_a_crafted_key_cannot_inject_query_parameters(self, monkeypatch):
        """The key lands in a query string. An admin pasting a mangled value
        (or a compromised settings row) must not be able to append parameters
        of its own and change what is being asked for."""
        _set_resolved_key(monkeypatch, "abc&resources=volume&limit=100")
        box = _capture_request(monkeypatch)

        _provider().search("Batman")

        assert "api_key=abc%26resources%3Dvolume%26limit%3D100" in box["url"]
        assert "resources=volume" not in box["url"], (
            "the injected parameter survived as a real parameter"
        )
        assert box["url"].count("resources=") == 1

    def test_no_class_attribute_bakes_a_key_into_a_url(self):
        """Source-pin: if a future edit moves the key back into a class-body
        f-string, the runtime resolution above is silently dead again."""
        for name in ("BASE_URL", "QUERY_PARAMS", "META_URL"):
            value = getattr(comicvine.ComicVine, name)
            assert "api_key" not in value, (
                f"{name} carries an api_key parameter, so it is interpolated "
                "at import time and cannot see a key configured later (#1242)."
            )

    def test_inactive_provider_issues_no_request(self, monkeypatch):
        _set_resolved_key(monkeypatch, "")
        box = _capture_request(monkeypatch)

        provider = comicvine.ComicVine()
        provider.active = False
        assert provider.search("Batman") == []
        assert "url" not in box


@pytest.mark.unit
class TestErrorState:
    """ComicVine reports its own failures in a 200 body, so an exhausted rate
    limit used to be indistinguishable from 'no matches' — the exact symptom
    the shared key causes, invisible in the log."""

    def _warnings(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            comicvine.log,
            "warning",
            lambda msg, *args: seen.append(str(msg) % args if args else str(msg)),
        )
        return seen

    def test_rate_limit_in_a_200_body_is_logged_with_the_remedy(self, monkeypatch):
        _set_resolved_key(monkeypatch, "")
        _capture_request(
            monkeypatch,
            payload={"error": "Rate Limit Exceeded", "status_code": 107,
                     "results": []},
        )
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert len(seen) == 1
        assert "Rate Limit Exceeded" in seen[0]
        assert "Keys panel" in seen[0], (
            "An install on the shared key needs to be told how to get its own "
            "quota, not just that something failed."
        )

    def test_rejected_own_key_names_the_install_key(self, monkeypatch):
        _set_resolved_key(monkeypatch, "a-wrong-key")
        _capture_request(
            monkeypatch,
            payload={"error": "Invalid API Key", "status_code": 100,
                     "results": []},
        )
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert len(seen) == 1
        assert "Invalid API Key" in seen[0]
        assert "Keys panel" not in seen[0], (
            "This install already has its own key; telling it to add one is "
            "the wrong remedy."
        )

    def test_ok_envelope_is_parsed_normally(self, monkeypatch):
        _set_resolved_key(monkeypatch, "")
        _capture_request(monkeypatch)
        seen = self._warnings(monkeypatch)

        results = _provider().search("Batman")

        assert seen == []
        assert len(results) == 1
        assert results[0].title == "Batman#1 - The Killing Joke"
        assert results[0].identifiers == {"comicvine": 12345}

    def test_envelope_without_status_code_is_not_treated_as_an_error(
        self, monkeypatch
    ):
        """Be tolerant of a shape change: absent status_code must not turn a
        working search into a silent empty."""
        _set_resolved_key(monkeypatch, "")
        payload = _ok_payload()
        payload.pop("status_code")
        _capture_request(monkeypatch, payload=payload)

        assert len(_provider().search("Batman")) == 1

    def test_transport_failure_still_degrades_quietly(self, monkeypatch):
        _set_resolved_key(monkeypatch, "")

        def boom(*_a, **_k):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(comicvine.requests, "get", boom)
        assert _provider().search("Batman") == []


@pytest.mark.unit
class TestHttpRefusalChannel:
    """ComicVine refuses on *two* channels. Verified against the live API
    2026-07-30: a rejected key comes back as an HTTP 401, not the in-body
    ``status_code`` the API documents. Handling only the envelope left the
    real, observed case logging a bare "401 Client Error" with no remedy.
    """

    def _warnings(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            comicvine.log,
            "warning",
            lambda msg, *args: seen.append(str(msg) % args if args else str(msg)),
        )
        return seen

    def _http_error(self, monkeypatch, status):
        response = types.SimpleNamespace(status_code=status)
        err = comicvine.requests.HTTPError(f"{status} Client Error: for url: x")
        err.response = response

        def fake_get(*_a, **_k):
            raise err

        monkeypatch.setattr(comicvine.requests, "get", fake_get)

    @pytest.mark.parametrize("status", [401, 403, 420, 429])
    def test_refusal_statuses_get_the_shared_key_remedy(self, monkeypatch, status):
        _set_resolved_key(monkeypatch, "")
        self._http_error(monkeypatch, status)
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert len(seen) == 1
        assert "Keys panel" in seen[0], (
            f"HTTP {status} is how ComicVine actually refuses; without this "
            "the user gets a bare status line and no remedy (#1242)."
        )

    def test_rejected_own_key_over_http_names_the_install_key(self, monkeypatch):
        _set_resolved_key(monkeypatch, "a-wrong-key")
        self._http_error(monkeypatch, 401)
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert "Check the ComicVine API key" in seen[0]
        assert "Keys panel" not in seen[0]

    def test_ordinary_http_failure_stays_a_bare_warning(self, monkeypatch):
        """A 500 is not a key problem; telling the user to go get a key would
        be a wrong remedy."""
        _set_resolved_key(monkeypatch, "")
        self._http_error(monkeypatch, 500)
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert len(seen) == 1
        assert "Keys panel" not in seen[0]

    def test_the_configured_key_is_never_written_to_the_log(self, monkeypatch):
        """The key travels in the query string and requests puts the full URL
        in its exception message, so every logged failure used to print it.
        Harmless while the only key was the public shared one; a real leak now
        that an install can configure its own, because admins paste logs into
        bug reports.
        """
        secret = "s3cret-install-key"
        _set_resolved_key(monkeypatch, secret)
        err = comicvine.requests.HTTPError(
            "401 Client Error: Unauthorized for url: "
            f"https://comicvine.gamespot.com/api/search/?api_key={secret}"
            "&resources=issue&query=Batman"
        )
        err.response = types.SimpleNamespace(status_code=401)

        def fake_get(*_a, **_k):
            raise err

        monkeypatch.setattr(comicvine.requests, "get", fake_get)
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert len(seen) == 1
        assert secret not in seen[0], "the install's API key leaked into the log"
        assert "api_key=***" in seen[0]
        assert "401" in seen[0], "redaction must not swallow the diagnosis"

    def test_shared_key_is_redacted_too(self, monkeypatch):
        """No exemption for the shared key: one rule is easier to keep than a
        rule with an exception, and the log stays readable either way."""
        _set_resolved_key(monkeypatch, "")
        err = comicvine.requests.HTTPError(
            "500 Server Error for url: "
            f"https://comicvine.gamespot.com/api/search/?api_key={SHARED_KEY}"
        )
        err.response = types.SimpleNamespace(status_code=500)

        def fake_get(*_a, **_k):
            raise err

        monkeypatch.setattr(comicvine.requests, "get", fake_get)
        seen = self._warnings(monkeypatch)

        assert _provider().search("Batman") == []
        assert SHARED_KEY not in seen[0]

    def test_canonical_url_avoids_the_redirect_hop(self, monkeypatch):
        """ComicVine 301s /api/search to /api/search/ — observed on the wire,
        so every search paid an extra round trip."""
        _set_resolved_key(monkeypatch, "")
        box = _capture_request(monkeypatch)

        _provider().search("Batman")

        assert box["url"].startswith(
            "https://comicvine.gamespot.com/api/search/?"
        )
