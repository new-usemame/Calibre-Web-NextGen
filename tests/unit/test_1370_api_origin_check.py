"""Regression tests for the blueprint-level cross-site guard (cps.api._reject_cross_site_mutation) — #1370.

The CSRF *token* was already enforced on /api/v1, but a request carrying a valid
token together with `Origin: https://evil.example` was accepted and performed the
write. Flask-WTF's own referer check is off here (`WTF_CSRF_SSL_STRICT=False` in
cps/__init__.py), so nothing looked at where the request claimed to come from.

The guard is deliberately "verify if present": a browser cannot suppress `Origin`
on a cross-site mutation, so checking it only when stated closes the browser CSRF
vector without breaking curl/native clients that send no such header (and which
carry no ambient credentials to be CSRF'd in the first place).
"""
import flask
import pytest
from unittest.mock import patch


def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app


def _gate(path, method, headers=None, base="http://cwng.local/"):
    """Call the guard directly under a request context, returning its verdict.

    Direct-call (rather than test_client) for the *pass* cases: on this bare app
    the view behind a passing gate would run without a login_manager or DB. Same
    pattern as test_api_v1_auth_gate.test_authenticated_request_passes_gate.
    """
    from cps.api import _reject_cross_site_mutation
    app = _app()
    with app.test_request_context(path, method=method, headers=headers or {},
                                  base_url=base):
        return _reject_cross_site_mutation()


# --- the reported symptom -------------------------------------------------

@pytest.mark.unit
def test_cross_site_origin_on_mutation_is_rejected():
    """#1370 as filed: a valid-token POST with a foreign Origin performed the write."""
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Origin": "https://evil.example"})
    assert verdict is not None, "cross-site mutation was allowed through the gate"
    body, status = verdict
    assert status == 403
    assert body.get_json()["error"]["code"] == "cross_site_request"


@pytest.mark.unit
def test_cross_site_origin_rejected_end_to_end_through_the_client():
    """Full request path: the guard short-circuits before the view, so a foreign
    Origin never reaches delete_tag at all.

    The auth gate is satisfied here on purpose. Without the guard the request gets
    past it and into the view — which is precisely the reported symptom (the write
    is performed) — so the pre-fix failure is 'the view was reached', not an
    incidental configuration error.
    """
    app = _app()
    with patch("cps.api.current_user") as cu, patch("cps.api.config") as cfg:
        cu.is_authenticated = True
        cfg.config_allow_reverse_proxy_header_login = False
        cfg.config_anonbrowse = 0
        resp = app.test_client().delete("/api/v1/tags/1",
                                        headers={"Origin": "https://evil.example"},
                                        base_url="http://cwng.local/")
    assert resp.status_code == 403, (
        f"cross-site DELETE was not rejected; reached the view and returned "
        f"{resp.status_code}")
    assert resp.is_json
    assert resp.get_json()["error"]["code"] == "cross_site_request"


@pytest.mark.unit
def test_cross_site_referer_without_origin_is_rejected():
    """Older browsers send Referer but not Origin; the guard falls back to it."""
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Referer": "https://evil.example/attack.html"})
    assert verdict is not None
    assert verdict[1] == 403


@pytest.mark.unit
def test_null_origin_on_mutation_is_rejected():
    """A sandboxed iframe / privacy-stripped request sends `Origin: null`. That is
    never our SPA, so it must not be treated as 'no header stated'."""
    verdict = _gate("/api/v1/tags/1", "POST", {"Origin": "null"})
    assert verdict is not None
    assert verdict[1] == 403


# --- what must keep working ----------------------------------------------

@pytest.mark.unit
def test_same_origin_mutation_passes():
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Origin": "http://cwng.local"})
    assert verdict is None


@pytest.mark.unit
def test_absent_origin_and_referer_passes():
    """curl and native clients send neither header. They also carry no ambient
    credentials, so they are not a CSRF vector — rejecting them would break real
    automation for no security gain."""
    assert _gate("/api/v1/tags/1", "POST") is None


@pytest.mark.unit
def test_safe_methods_are_never_rejected():
    """GET/HEAD/OPTIONS change nothing, and browsers omit Origin on same-origin
    GET, so checking it there would reject ordinary reads."""
    for method in ("GET", "HEAD", "OPTIONS"):
        assert _gate("/api/v1/tags", method,
                     {"Origin": "https://evil.example"}) is None, method


@pytest.mark.unit
def test_same_host_referer_with_subpath_passes():
    """Behind a subpath reverse proxy the Referer carries a path prefix. Only
    scheme+host are compared, so the prefix must not cause a false rejection."""
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Referer": "http://cwng.local/calibre/library"})
    assert verdict is None


@pytest.mark.unit
def test_default_port_is_not_a_mismatch():
    """`https://h` and `https://h:443` are the same origin; a naive string
    compare would reject the explicit form."""
    assert _gate("/api/v1/tags/1", "POST",
                 {"Origin": "https://cwng.local:443"},
                 base="https://cwng.local/") is None
    assert _gate("/api/v1/tags/1", "POST",
                 {"Origin": "https://cwng.local"},
                 base="https://cwng.local:443/") is None


@pytest.mark.unit
def test_scheme_mismatch_is_rejected():
    """Same host over a different scheme is a different origin."""
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Origin": "http://cwng.local"},
                    base="https://cwng.local/")
    assert verdict is not None
    assert verdict[1] == 403


@pytest.mark.unit
def test_host_prefix_is_not_treated_as_same_origin():
    """`cwng.local.evil.example` must not match `cwng.local` — a prefix/substring
    compare is the classic hole in hand-rolled origin checks."""
    for hostile in ("http://cwng.local.evil.example",
                    "http://evil.example/?x=http://cwng.local",
                    "http://notcwng.local"):
        verdict = _gate("/api/v1/tags/1", "POST", {"Origin": hostile})
        assert verdict is not None, hostile
        assert verdict[1] == 403, hostile


@pytest.mark.unit
def test_malformed_origin_is_rejected_not_crashed():
    """A garbage or port-less-but-colon'd Origin must produce a 403, never a 500."""
    for junk in ("http://cwng.local:notaport", "://", "evil.example", ""):
        verdict = _gate("/api/v1/tags/1", "POST", {"Origin": junk})
        if junk == "":
            # An empty header is indistinguishable from an absent one.
            assert verdict is None, junk
        else:
            assert verdict is not None, junk
            assert verdict[1] == 403, junk


# --- reverse-proxy deployments -------------------------------------------

@pytest.mark.unit
def test_x_forwarded_host_deployment_passes_via_host_url():
    """ProxyFix (x_host=1, cps/__init__.py) folds X-Forwarded-Host into host_url, so
    the public origin the browser used is what we compare against."""
    verdict = _gate("/api/v1/tags/1", "POST",
                    {"Origin": "https://books.example.com"},
                    base="https://books.example.com/")
    assert verdict is None


@pytest.mark.unit
def test_trusted_origins_env_var_rescues_a_host_rewriting_proxy():
    """The one setup host_url cannot infer: a proxy that rewrites Host and sends no
    X-Forwarded-Host, so host_url is the internal name. Without this escape hatch
    those users would get a 403 on every write.

    Patches the resolved tuple rather than reloading cps.api: a reload rebuilds
    api_v1 while the route submodules stay bound to the old blueprint in
    sys.modules, which silently un-registers every route.
    """
    from cps.api import _reject_cross_site_mutation
    app = _app()
    with patch("cps.api._EXTRA_TRUSTED_ORIGINS", ("https://books.example.com",)):
        with app.test_request_context("/api/v1/tags/1", method="POST",
                                      headers={"Origin": "https://books.example.com"},
                                      base_url="http://calibre-web:8083/"):
            assert _reject_cross_site_mutation() is None
        # A still-foreign origin is not waved through just because the var is set.
        with app.test_request_context("/api/v1/tags/1", method="POST",
                                      headers={"Origin": "https://evil.example"},
                                      base_url="http://calibre-web:8083/"):
            assert _reject_cross_site_mutation() is not None


@pytest.mark.unit
def test_trusted_origins_is_unset_by_default():
    """OSS-friendly default: no env var is required for a normal deployment."""
    from cps.api import _EXTRA_TRUSTED_ORIGINS
    assert _EXTRA_TRUSTED_ORIGINS == ()


@pytest.mark.unit
def test_trusted_origins_parsing():
    from cps.api import _parse_trusted_origins
    assert _parse_trusted_origins(None) == ()
    assert _parse_trusted_origins("") == ()
    assert _parse_trusted_origins("  ") == ()
    assert _parse_trusted_origins("https://a.example") == ("https://a.example",)
    assert _parse_trusted_origins(" https://a.example , https://b.example:8443 ,,") == (
        "https://a.example", "https://b.example:8443")


# --- blueprint-wide, not per-route ---------------------------------------

@pytest.mark.unit
def test_guard_is_registered_blueprint_wide():
    """#1370 asked for one hook covering every mutating route rather than a check
    bolted onto whichever route was touched last. Pin that it is a before_request
    on api_v1 itself, so a new route inherits it without opting in."""
    from cps.api import _reject_cross_site_mutation
    # A blueprint's before_request handlers only land in before_request_funcs
    # once the blueprint is registered, so assert against a real app.
    app = _app()
    handlers = [f.__name__ for f in app.before_request_funcs.get("api_v1", [])]
    assert _reject_cross_site_mutation.__name__ in handlers


@pytest.mark.unit
def test_guard_runs_before_the_auth_gate():
    """Order matters: the cross-site verdict should not depend on whether the
    forged request happened to be authenticated, and it must also cover the
    public endpoints (a cross-site POST to auth_login is login-CSRF)."""
    app = _app()
    handlers = [f.__name__ for f in app.before_request_funcs.get("api_v1", [])]
    assert handlers.index("_reject_cross_site_mutation") < handlers.index("_require_api_auth")


@pytest.mark.unit
def test_public_endpoint_mutation_is_still_origin_checked():
    """auth_login is in _PUBLIC_ENDPOINTS for the auth gate; that must not exempt
    it from the origin check."""
    app = _app()
    with patch("cps.api.config") as cfg:
        cfg.config_allow_reverse_proxy_header_login = False
        cfg.config_anonbrowse = 0
        resp = app.test_client().post("/api/v1/auth/login",
                                      headers={"Origin": "https://evil.example"},
                                      base_url="http://cwng.local/")
    assert resp.status_code == 403, (
        f"cross-site login POST was not rejected; returned {resp.status_code}")
    assert resp.get_json()["error"]["code"] == "cross_site_request"
