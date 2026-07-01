"""Regression tests for #571 — the new UI was a white page behind a reverse
proxy with a path prefix (e.g. https://host/cwa/) because the built SPA shell
hardcodes root-absolute asset URLs (/static/app/…) and the runtime hardcoded
/api/v1, the wouter router base /app and /cover|/download|/read resource URLs —
all missing the proxy prefix, so they 404'd.

Server side (this file): the shell is now served through Flask, which rewrites
the asset URLs to <prefix>/static/app/… and injects window.__CWNG_PREFIX__ from
request.script_root (the same value url_for uses for the classic UI, set by
ReverseProxied / ProxyFix upstream). Client side is source-pinned below.
"""
import pathlib
import re

import flask
import pytest

REALISTIC_INDEX = (
    '<!doctype html><html lang="en"><head>'
    '<meta charset="UTF-8" />'
    '<title>Calibre-Web NextGen</title>'
    '<script type="module" crossorigin src="/static/app/assets/index-abc.js"></script>'
    '<link rel="stylesheet" crossorigin href="/static/app/assets/index-def.css">'
    '</head><body><div id="root"></div></body></html>'
)


def _spa_app(monkeypatch, tmp_path, reverseproxied=False):
    import cps.spa as spa_mod
    (tmp_path / "index.html").write_text(REALISTIC_INDEX)
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "1")
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    if reverseproxied:
        from cps.reverseproxy import ReverseProxied
        app.wsgi_app = ReverseProxied(app.wsgi_app)
    return app


@pytest.mark.unit
def test_no_prefix_serves_shell_unchanged_but_injects_empty(monkeypatch, tmp_path):
    """At the domain root the asset URLs are untouched and the prefix global is
    injected as "" (authoritative — the SPA shouldn't have to guess)."""
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '/static/app/assets/index-abc.js' in body
    assert 'window.__CWNG_PREFIX__="";' in body
    assert "/cwa/" not in body


@pytest.mark.unit
def test_prefix_rewrites_assets_and_injects(monkeypatch, tmp_path):
    """Behind a subpath (script_root=/cwa) every /static/app/ asset URL gains the
    prefix and window.__CWNG_PREFIX__ is set to it. This is the core #571 fix; on
    main (raw send_from_directory) the file is served verbatim and this fails."""
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app", environ_overrides={"SCRIPT_NAME": "/cwa"})
    body = resp.get_data(as_text=True)
    assert 'src="/cwa/static/app/assets/index-abc.js"' in body
    assert 'href="/cwa/static/app/assets/index-def.css"' in body
    assert 'window.__CWNG_PREFIX__="/cwa";' in body
    # No leftover un-prefixed asset URL.
    assert 'src="/static/app/' not in body


@pytest.mark.unit
def test_prefix_via_reverseproxied_header(monkeypatch, tmp_path):
    """Faithful to production: the real ReverseProxied middleware reads
    X-Script-Name → SCRIPT_NAME → request.script_root, and the shell picks it up.
    Mirrors the reporter's nginx sending the prefix as a header."""
    app = _spa_app(monkeypatch, tmp_path, reverseproxied=True)
    resp = app.test_client().get("/cwa/app", headers={"X-Script-Name": "/cwa"})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '/cwa/static/app/assets/index-abc.js' in body
    assert 'window.__CWNG_PREFIX__="/cwa";' in body


@pytest.mark.unit
def test_trailing_slash_prefix_normalized(monkeypatch, tmp_path):
    """A prefix arriving with a trailing slash (X-Forwarded-Prefix: /cwa/) must
    not produce //static/app or a doubled slash in the injected value."""
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app", environ_overrides={"SCRIPT_NAME": "/cwa/"})
    body = resp.get_data(as_text=True)
    assert 'window.__CWNG_PREFIX__="/cwa";' in body
    assert "//static/app" not in body
    assert '/cwa/static/app/assets/index-abc.js' in body


@pytest.mark.unit
@pytest.mark.parametrize("bad", ['/a"><script>evil</script>', "/a b", "/a;b", "/../etc"])
def test_malicious_prefix_rejected(monkeypatch, tmp_path, bad):
    """A spoofed/garbage script_root can't break out of the injected <script> or
    poison the asset rewrite — it's rejected to "" and assets stay root-relative."""
    app = _spa_app(monkeypatch, tmp_path)
    resp = app.test_client().get("/app", environ_overrides={"SCRIPT_NAME": bad})
    body = resp.get_data(as_text=True)
    assert 'window.__CWNG_PREFIX__="";' in body
    assert "evil" not in body
    assert 'src="/static/app/assets/index-abc.js"' in body  # untouched


@pytest.mark.unit
@pytest.mark.parametrize("value,ok", [
    ("/cwa", True), ("/a/b/c", True), ("/lib_2", True),
    ("/cwa\n", False),          # trailing newline must not slip past the anchor
    ("/cwa\n/evil", False), ("//evil.com", False), ("/../etc", False),
    ("/a b", False), ('/a"x', False), ("", False),
])
def test_safe_prefix_regex(value, ok):
    """Direct check of the prefix allowlist, incl. the \\Z anchor rejecting a
    trailing newline (defence-in-depth; HTTP headers can't carry raw newlines)."""
    import cps.spa as spa_mod
    matched = bool(spa_mod._SAFE_PREFIX_RE.match(value)) and ".." not in value
    assert matched is ok


# ---- client-side source pins (guard the runtime prefix wiring) ---------------

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.unit
def test_api_ts_wraps_fetches_with_prefix():
    """api.ts must derive BASE_PREFIX and prefix every fetch via apiUrl(), else
    API calls 404 behind a subpath. resourceUrl() must exist for cover/download."""
    src = (_FE / "lib" / "api.ts").read_text()
    assert "__CWNG_PREFIX__" in src
    assert "export const BASE_PREFIX" in src
    assert "export function apiUrl" in src
    assert "export function resourceUrl" in src
    # Every fetch( in api.ts must go through apiUrl(...) — no bare fetch(path.
    for m in re.finditer(r"fetch\(([^)]*)", src):
        arg = m.group(1)
        assert "apiUrl(" in arg, f"un-prefixed fetch in api.ts: fetch({arg}"


@pytest.mark.unit
def test_app_router_base_uses_prefix():
    src = (_FE / "App.tsx").read_text()
    assert "BASE_PREFIX + '/app'" in src
    assert 'base="/app"' not in src  # the old hardcoded base must be gone


@pytest.mark.unit
def test_resource_url_is_idempotent():
    """resourceUrl must not double-prefix a value that already carries the mount
    prefix (e.g. a Flask url_for path from an apply endpoint) → /cwa/cwa/…."""
    src = (_FE / "lib" / "api.ts").read_text()
    assert "u.startsWith(BASE_PREFIX + '/')" in src, "resourceUrl missing double-prefix guard"


@pytest.mark.unit
def test_notfound_prefix_matched_literally():
    """The NotFound legacy-link must strip the <prefix>/app base with a literal
    string compare, not an unescaped RegExp (a dotted prefix like /app.v2 would
    otherwise match loosely)."""
    src = (_FE / "pages" / "NotFound.tsx").read_text()
    assert "startsWith(appBase)" in src
    assert "new RegExp(" not in src


@pytest.mark.unit
def test_resource_urls_prefixed_at_consumption():
    """Covers/downloads served by the backend must be routed through resourceUrl
    so they resolve under the mount prefix."""
    bc = (_FE / "components" / "BookCover.tsx").read_text()
    assert "resourceUrl(coverUrl)" in bc
    detail = (_FE / "pages" / "BookDetail.tsx").read_text()
    assert "resourceUrl(book.cover_url)" in detail
    assert "resourceUrl(fmt.download_url)" in detail


@pytest.mark.unit
def test_vite_runtime_asset_base_is_prefix_aware():
    """The lazy chunk loader must derive asset URLs from window.__CWNG_PREFIX__ at
    runtime (renderBuiltUrl), else code-split JS/CSS (the readers) 404 behind a
    proxy prefix and render unstyled — the server index.html rewrite can't reach
    runtime-computed URLs. Regression guard for the v4.1.1 reader-CSS bug."""
    cfg = (_FE.parent / "vite.config.ts").read_text()
    assert "renderBuiltUrl" in cfg
    assert "__CWNG_PREFIX__" in cfg
