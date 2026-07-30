# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1248 — ReverseProxied must strip the mount prefix only on a path-segment
boundary.

Reported by @chloeroform on v4.1.25: behind a reverse proxy with
`PROXY_SCRIPT_NAME=/cwa`, every "More server configuration" link on the admin
page 404s — `/cwa-settings`, `/cwa-settings#duplicate-detection`,
`/cwa-stats-show` — while the neighbouring `/admin/*` links on the same page
work fine.

Root cause. The middleware stripped the prefix on a bare `str.startswith()`
match:

    if path_info.startswith(script_name):
        environ['PATH_INFO'] = path_info[len(script_name):]

That predicate is true for any path that merely *shares the prefix's
characters*, not just for genuine child paths. This repo has 37 routes whose
paths begin `/cwa-` (the entire `cps/cwa_functions.py` surface), so with a
`/cwa` mount:

    PATH_INFO=/cwa-settings  →  '-settings'   →  404
    PATH_INFO=/admin/config  →  untouched     →  200

which is exactly the asymmetry the reporter saw. The `/admin/*` links working
is the diagnostic wedge, not a coincidence.

Why the reporter's PATH_INFO arrives already stripped: the README's documented
nginx recipe uses `proxy_pass http://…:8083/;` (trailing slash), which strips
`/cwa` at the proxy, plus `PROXY_SCRIPT_NAME=/cwa` so `url_for` still emits
prefixed URLs. Both prefix-stripping and non-stripping proxy modes must work,
and a segment-boundary check satisfies both:

  * stripping proxy      → PATH_INFO=/cwa-settings      → no boundary → keep
  * non-stripping proxy  → PATH_INFO=/cwa/cwa-settings  → boundary    → strip

The SPA's own idempotency guard already got this boundary right
(`frontend/src/lib/api.ts::resourceUrl`, `u === BASE_PREFIX ||
u.startsWith(BASE_PREFIX + '/')`, pinned by
`tests/unit/test_571_reverse_proxy_prefix.py`). The WSGI middleware did not.
These tests close that asymmetry.

Also pinned here: PEP 3333 normalisation. `SCRIPT_NAME` must not end in `/`
and a non-empty `PATH_INFO` must start with `/`. Operators routinely write
`PROXY_SCRIPT_NAME=/cwa/`, which previously produced `SCRIPT_NAME='/cwa/'` and
`PATH_INFO='books'` — a second, independent 404 class. `cps/spa.py` already
defends itself with `request.script_root.rstrip('/')`; the middleware is the
right place to normalise once.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CPS_DIR = REPO_ROOT / "cps"

PROXY_ENV_VARS = ("PROXY_SCRIPT_NAME", "PROXY_SCHEME", "PROXY_HOST", "PROXY_PORT")


def _make_environ(**overrides):
    base = {"PATH_INFO": "/", "REQUEST_METHOD": "GET"}
    base.update(overrides)
    return base


def _call_middleware(monkeypatch, env_vars, environ):
    """Instantiate ReverseProxied with the given env and invoke it, returning
    the mutated environ."""
    from cps import reverseproxy

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    for key in PROXY_ENV_VARS:
        if key not in env_vars:
            monkeypatch.delenv(key, raising=False)

    middleware = reverseproxy.ReverseProxied(MagicMock(return_value=[b""]))
    middleware(environ, MagicMock())
    return environ


def _cwa_route_rules():
    """Every route rule in cps/ whose path begins with '/cwa'.

    Source-derived on purpose: this is the real blast radius, so the pin grows
    automatically when someone adds another /cwa-* route.
    """
    pattern = re.compile(r"""@\w+\.route\(\s*['"](/cwa[^'"]*)['"]""")
    rules = set()
    for path in sorted(CPS_DIR.glob("*.py")):
        rules.update(pattern.findall(path.read_text()))
    return sorted(rules)


def test_the_blast_radius_is_real_and_nonempty():
    """Guard the guard: if the route-scrape ever returns nothing, the
    blast-radius test below would pass vacuously."""
    rules = _cwa_route_rules()
    assert len(rules) >= 30, (
        "Expected the /cwa-* route surface (cps/cwa_functions.py) to be "
        f"scrapeable; found only {len(rules)}: {rules}"
    )
    assert "/cwa-settings" in rules


@pytest.mark.parametrize("rule", _cwa_route_rules())
def test_no_cwa_route_is_mangled_by_a_cwa_mount(monkeypatch, rule):
    """#1248 — the reporter's exact configuration, applied to all 37 routes.

    A prefix-stripping proxy hands us the route path verbatim. None of these
    may be rewritten: they share characters with the mount prefix but are not
    children of it.
    """
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa"},
        _make_environ(PATH_INFO=rule),
    )

    assert environ["PATH_INFO"] == rule, (
        f"{rule!r} shares characters with the /cwa mount prefix but is not a "
        f"child of it, so PATH_INFO must survive untouched. Got "
        f"{environ['PATH_INFO']!r} — that path matches no route and 404s."
    )
    assert environ["SCRIPT_NAME"] == "/cwa", (
        "SCRIPT_NAME must still be set so url_for() emits prefixed URLs."
    )


def test_reported_symptom_cwa_settings_does_not_404(monkeypatch):
    """The literal link from the issue: admin page → 'CWA settings'."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa"},
        _make_environ(PATH_INFO="/cwa-settings"),
    )
    assert environ["PATH_INFO"] == "/cwa-settings"


def test_neighbouring_admin_link_still_works(monkeypatch):
    """The diagnostic wedge — /admin/* never matched the prefix, which is why
    the reporter saw some links work and others 404."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa"},
        _make_environ(PATH_INFO="/admin/config"),
    )
    assert environ["PATH_INFO"] == "/admin/config"


def test_genuine_child_path_is_still_stripped(monkeypatch):
    """Regression guard for the non-stripping proxy mode. This is the whole
    reason the middleware strips at all — do not lose it while fixing #1248."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa"},
        _make_environ(PATH_INFO="/cwa/cwa-settings"),
    )
    assert environ["PATH_INFO"] == "/cwa-settings", (
        "A real child path must still have the mount prefix removed, or the "
        "non-stripping proxy deployment breaks."
    )
    assert environ["SCRIPT_NAME"] == "/cwa"


def test_child_path_stripped_for_ordinary_route(monkeypatch):
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/myprefix"},
        _make_environ(PATH_INFO="/myprefix/books"),
    )
    assert environ["PATH_INFO"] == "/books"


def test_mount_root_collapses_to_empty_path(monkeypatch):
    """PATH_INFO == SCRIPT_NAME is the mount root; werkzeug redirects '' to
    the trailing-slash form. Preserve the pre-fix behaviour here."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa"},
        _make_environ(PATH_INFO="/cwa"),
    )
    assert environ["PATH_INFO"] == ""
    assert environ["SCRIPT_NAME"] == "/cwa"


def test_header_script_name_is_boundary_checked_too(monkeypatch):
    """The X-Script-Name path is the same code; a header-configured proxy must
    not mangle /cwa-* either."""
    environ = _call_middleware(
        monkeypatch,
        {},
        _make_environ(HTTP_X_SCRIPT_NAME="/cwa", PATH_INFO="/cwa-settings"),
    )
    assert environ["PATH_INFO"] == "/cwa-settings"
    assert environ["SCRIPT_NAME"] == "/cwa"


def test_trailing_slash_in_env_prefix_is_normalized(monkeypatch):
    """PEP 3333: SCRIPT_NAME must not end in '/', and a non-empty PATH_INFO
    must start with '/'. `PROXY_SCRIPT_NAME=/cwa/` previously produced
    SCRIPT_NAME='/cwa/' and PATH_INFO='books' — routing 404s on both counts."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/cwa/"},
        _make_environ(PATH_INFO="/cwa/books"),
    )
    assert environ["SCRIPT_NAME"] == "/cwa", (
        "SCRIPT_NAME must be normalised to have no trailing slash (PEP 3333)."
    )
    assert environ["PATH_INFO"] == "/books", (
        "PATH_INFO must retain its leading slash after the strip (PEP 3333)."
    )


def test_trailing_slash_in_header_prefix_is_normalized(monkeypatch):
    environ = _call_middleware(
        monkeypatch,
        {},
        _make_environ(HTTP_X_SCRIPT_NAME="/cwa/", PATH_INFO="/cwa-settings"),
    )
    assert environ["SCRIPT_NAME"] == "/cwa"
    assert environ["PATH_INFO"] == "/cwa-settings"


def test_root_mount_is_not_treated_as_a_prefix(monkeypatch):
    """PROXY_SCRIPT_NAME=/ means "mounted at root", i.e. no prefix at all.
    Normalisation turns it into '' so nothing is rewritten."""
    environ = _call_middleware(
        monkeypatch,
        {"PROXY_SCRIPT_NAME": "/"},
        _make_environ(PATH_INFO="/cwa-settings"),
    )
    assert environ["PATH_INFO"] == "/cwa-settings"
    assert environ.get("SCRIPT_NAME", "") in ("", None), (
        "A root mount must not set a SCRIPT_NAME prefix."
    )


def test_no_proxy_config_leaves_environ_alone(monkeypatch):
    environ = _call_middleware(monkeypatch, {}, _make_environ(PATH_INFO="/cwa-settings"))
    assert environ["PATH_INFO"] == "/cwa-settings"
    assert "SCRIPT_NAME" not in environ


def test_source_documents_the_boundary_requirement():
    """Refactor-fragile invariant: a future edit that reverts to a bare
    startswith() should have to delete this reasoning first."""
    src = (CPS_DIR / "reverseproxy.py").read_text()
    assert "1248" in src, (
        "cps/reverseproxy.py must reference #1248 so the segment-boundary "
        "requirement survives a future refactor of the strip."
    )
    assert not re.search(
        r"startswith\(\s*script_name\s*\)", src
    ), (
        "A bare `startswith(script_name)` is the #1248 defect: it fires on "
        "sibling paths that merely share the prefix's characters. Match on a "
        "path-segment boundary (equality, or prefix + '/')."
    )
