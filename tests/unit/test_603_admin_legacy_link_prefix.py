# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #603 — the tail of #571.

Behind a sub-path reverse proxy (app mounted at https://host/cwa/), the new UI's
Admin page rendered its "More server configuration" cards as raw
``<a href="/admin/config">`` etc. — root-absolute legacy paths that skipped the
reverse-proxy prefix helper #571 wired into the rest of the app. So a card that
should point at ``/cwa/admin/config`` pointed at the domain root, landing outside
the mount (404 / breaks out of the app). Reporter @chloeroform pinned the exact
line (Admin.tsx server-config map).

The fix routes those hrefs through ``resourceUrl()`` — the single-source-of-truth
prefix helper in api.ts (idempotent, leaves external/data URLs untouched, and a
no-op when the mount prefix is empty, so root-mount installs are unaffected).

Client-side source pins (the SPA bundle is built in Docker, not committed, so a
runtime assertion isn't reachable here — pin the source that the build compiles).
The audit-around-the-ask siblings (OAuth buttons, the 404 fallback) are pinned
too so the same gap can't reopen next door.
"""
import pathlib
import re

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_ADMIN = _FE / "pages" / "Admin.tsx"


def _admin_src() -> str:
    return _ADMIN.read_text()


@pytest.mark.unit
def test_admin_imports_resource_url():
    """Admin.tsx must import the prefix helper it now depends on."""
    src = _admin_src()
    assert re.search(r"import\s*{[^}]*\bresourceUrl\b[^}]*}\s*from\s*'\.\./lib/api'", src), \
        "Admin.tsx must import resourceUrl from ../lib/api"


@pytest.mark.unit
def test_server_config_links_go_through_resource_url():
    """The 'More server configuration' cards must build their href via
    resourceUrl(href). RED on main, where the anchor rendered raw href={href}."""
    src = _admin_src()
    assert "href={resourceUrl(href)}" in src, \
        "server-config card anchor must use href={resourceUrl(href)}"
    # The raw, un-prefixed form must be gone — this is the exact #603 bug.
    assert "href={href}" not in src, \
        "raw href={href} on the settings card leaks the reverse-proxy prefix (#603)"


@pytest.mark.unit
def test_server_settings_are_prefixable_app_paths():
    """Every SERVER_SETTINGS entry must be a root-absolute in-app legacy path
    (starts with '/', not an external/protocol-relative/data URL) so resourceUrl
    actually applies the mount prefix. If one were made external, resourceUrl
    would (correctly) leave it alone and the pin above would be a false comfort."""
    src = _admin_src()
    block = re.search(r"const SERVER_SETTINGS[^=]*=\s*\[(.*?)\];", src, re.S)
    assert block, "SERVER_SETTINGS array not found"
    hrefs = re.findall(r"href:\s*'([^']+)'", block.group(1))
    assert len(hrefs) >= 5, f"expected the full legacy-config set, found {hrefs}"
    for h in hrefs:
        assert h.startswith("/"), f"{h!r} is not a root-absolute path"
        assert not re.match(r"^(https?:)?//", h), f"{h!r} is external — resourceUrl no-ops it"
        assert not h.startswith("data:"), f"{h!r} is a data URL"


@pytest.mark.unit
def test_resource_url_contract_holds():
    """The safety of wrapping every legacy link in resourceUrl rests on its
    contract: leave absolute/data URLs untouched, don't double-prefix, and be a
    no-op at the root mount. Pin that contract so a future api.ts refactor can't
    silently turn the wrap into a mangler."""
    src = (_FE / "lib" / "api.ts").read_text()
    assert "export function resourceUrl" in src
    # external / protocol-relative / data URLs left untouched
    assert "startsWith('data:')" in src
    assert r"/^(https?:)?\/\//i" in src
    # idempotent: value already carrying the prefix is not prefixed again
    assert "u.startsWith(BASE_PREFIX + '/')" in src
    # empty prefix ⇒ BASE_PREFIX + u == u (no-op at the root mount)
    assert "export const BASE_PREFIX" in src


@pytest.mark.unit
def test_audit_siblings_remain_prefix_safe():
    """Audit-around-the-ask: the other native <a href> links out to legacy routes
    must stay prefix-safe so #603's class can't reopen next door.

    - NotFound's 'classic interface' link builds BASE_PREFIX + afterApp itself.
    - The OAuth provider buttons render p.url, which the backend builds with
      Flask url_for (script_root-aware) — pin that server side so it can't
      regress to a root-absolute literal that would strip the prefix.
    """
    notfound = (_FE / "pages" / "NotFound.tsx").read_text()
    assert "BASE_PREFIX + afterApp" in notfound, "NotFound legacy link must carry the prefix"

    auth = (pathlib.Path(__file__).resolve().parents[2] / "cps" / "api" / "auth.py").read_text()
    # Capture the whole _oauth_providers body: from its def to the next top-level def.
    body = re.search(r"def _oauth_providers\(.*?(?=\n(?:def |@|\Z))", auth, re.S)
    assert body, "_oauth_providers not found in cps/api/auth.py"
    assert "url_for(" in body.group(0), \
        "OAuth provider urls must be built with url_for so they carry the mount prefix"
