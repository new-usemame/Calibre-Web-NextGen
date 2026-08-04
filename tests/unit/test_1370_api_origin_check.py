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
@pytest.mark.parametrize("fwd_host", ["books.example.com", "books.example.com:8443"])
def test_real_proxyfix_forwarded_host_deployment_passes(fwd_host):
    """Drive the actual ProxyFix middleware, from an internal base URL, so this
    stays honest: it fails if the production wrapping is removed. Both forms are
    covered because a proxy very often strips the port from X-Forwarded-Host while
    the browser still states it — comparing ports would 403 that deployment.
    """
    from werkzeug.middleware.proxy_fix import ProxyFix
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.register_blueprint(api_v1)

    with patch("cps.api.current_user") as cu, patch("cps.api.config") as cfg:
        cu.is_authenticated = True
        cfg.config_allow_reverse_proxy_header_login = False
        cfg.config_anonbrowse = 0
        # Internal base URL: only the forwarded headers can make this match.
        resp = app.test_client().post(
            "/api/v1/tags/1",
            headers={"Origin": "https://books.example.com:8443",
                     "X-Forwarded-Host": fwd_host,
                     "X-Forwarded-Proto": "https"},
            base_url="http://calibre-web:8083/")
    assert resp.status_code != 403, (
        f"legitimate proxied write rejected with X-Forwarded-Host: {fwd_host}")

    # Control: the same middleware still rejects a genuinely foreign origin.
    with patch("cps.api.current_user") as cu, patch("cps.api.config") as cfg:
        cu.is_authenticated = True
        cfg.config_allow_reverse_proxy_header_login = False
        cfg.config_anonbrowse = 0
        resp = app.test_client().post(
            "/api/v1/tags/1",
            headers={"Origin": "https://evil.example",
                     "X-Forwarded-Host": fwd_host,
                     "X-Forwarded-Proto": "https"},
            base_url="http://calibre-web:8083/")
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "cross_site_request"


@pytest.mark.unit
def test_port_is_not_part_of_the_comparison():
    """Explicit, since a proxy stripping the port is the common breakage. Also pins
    that an explicit `:0` cannot be coerced into matching the scheme default."""
    assert _gate("/api/v1/tags/1", "POST",
                 {"Origin": "http://cwng.local:8443"}) is None
    assert _gate("/api/v1/tags/1", "POST",
                 {"Origin": "http://cwng.local:0"}) is None
    # Host still governs, whatever the port.
    verdict = _gate("/api/v1/tags/1", "POST", {"Origin": "http://evil.example:80"})
    assert verdict is not None and verdict[1] == 403


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
def test_guard_runs_after_the_auth_gate():
    """Order matters, and auth must come first. Running the origin guard first made
    it an oracle — varying Origin on an unauthenticated request returns 403 for an
    untrusted origin and 401 for a trusted one, disclosing which origins are
    trusted — and it replaced the API's documented 401 with a 403."""
    app = _app()
    handlers = [f.__name__ for f in app.before_request_funcs.get("api_v1", [])]
    assert handlers.index("_require_api_auth") < handlers.index("_reject_cross_site_mutation")


@pytest.mark.unit
def test_unauthenticated_protected_route_still_answers_401_not_403():
    """The auth gate's contract survives: an SPA fetch on an expired session must
    get the JSON 401 it knows how to act on, even when it states a foreign origin."""
    app = _app()
    with patch("cps.api.current_user") as cu, patch("cps.api.config") as cfg:
        cu.is_authenticated = False
        cfg.config_allow_reverse_proxy_header_login = False
        cfg.config_anonbrowse = 0
        resp = app.test_client().post("/api/v1/tags/1",
                                      headers={"Origin": "https://evil.example"},
                                      base_url="http://cwng.local/")
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "unauthorized"


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
