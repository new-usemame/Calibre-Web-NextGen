# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #603 — the tail of #571.

Behind a sub-path reverse proxy (app mounted at https://host/cwa/), the new UI's
Admin surface rendered its "More server configuration" links as raw
``<a href="/admin/config">`` etc. — root-absolute legacy paths that skipped the
reverse-proxy prefix helper #571 wired into the rest of the app. So a card that
should point at ``/cwa/admin/config`` pointed at the domain root, landing outside
the mount (404 / breaks out of the app). Reporter @chloeroform pinned the exact
line (the original Admin.tsx server-config map).

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
_CONTEXTS = _FE / "lib" / "contextSidebars.ts"
_SIDEBAR = _FE / "components" / "ContextSidebar.tsx"


def _context_src() -> str:
    return _CONTEXTS.read_text()


def _sidebar_src() -> str:
    return _SIDEBAR.read_text()


def _navigation_items() -> list[str]:
    """Return every leaf context-navigation item containing an href."""
    return re.findall(r"\{[^{}]*\bhref:\s*'[^']+'[^{}]*\}", _context_src(), re.S)


@pytest.mark.unit
def test_admin_context_sidebar_imports_resource_url():
    """The context-sidebar renderer must import its legacy-link prefix helper."""
    src = _sidebar_src()
    assert re.search(r"import\s*{[^}]*\bresourceUrl\b[^}]*}\s*from\s*'\.\./lib/api'", src), \
        "ContextSidebar.tsx must import resourceUrl from ../lib/api"


@pytest.mark.unit
def test_server_config_links_go_through_resource_url():
    """Classic Admin context links must build their href via resourceUrl()."""
    src = _sidebar_src()
    assert "href={resourceUrl(item.href)}" in src, \
        "classic context link must use href={resourceUrl(item.href)}"
    # A raw legacy <a href={item.href}> must stay gone — this is the exact #603
    # bug. Native destinations legitimately use Wouter <Link href={item.href}>;
    # its Router
    # base adds /app and the reverse-proxy prefix (#909).
    assert not re.search(r"<a\b[^>]*href=\{item\.href\}", src, re.S), \
        "raw legacy <a href={item.href}> leaks the reverse-proxy prefix (#603)"
    assert "href={item.href}" in src and "<Link" in src, \
        "native settings destinations must stay inside the SPA router"


@pytest.mark.unit
def test_no_server_setting_falls_through_to_classic_when_a_spa_route_exists():
    """#909's real invariant, stated generically rather than pinned to one row.

    A card may only be a Classic fall-through (no `spa: true`) when the SPA has
    no native route for that path. #909 pinned this by asserting the literal
    "Duplicate books" row; #1048 replaced that row (it pointed at /duplicates,
    the exact destination the sidebar already offers, so the admin entry did
    nothing new) with a deep link to the duplicate-detection *settings*, which
    has no SPA page. Pinning the old literal would forbid that correct change,
    so pin the rule instead: cross-check every Classic row against the SPA's own
    route table.
    """
    routes = (_FE / "lib" / "routes.ts").read_text()
    spa_paths = set(re.findall(r":\s*'(/[^']*)'", routes))
    assert "/duplicates" in spa_paths, "sanity: routes.ts should still own /duplicates"

    items = _navigation_items()
    assert items, "context navigation items not found"
    for entry in items:
        href = re.search(r"href:\s*'([^']+)'", entry).group(1)
        path = href.split("#")[0].split("?")[0]
        if "classic: true" not in entry:
            continue
        assert path not in spa_paths, (
            f"{path!r} has a native SPA route but this item falls through to "
            f"Classic (#909). Mark it `spa: true` or point it somewhere else."
        )


@pytest.mark.unit
def test_server_settings_are_prefixable_app_paths():
    """Every classic context item must be a root-absolute in-app legacy path
    (starts with '/', not an external/protocol-relative/data URL) so resourceUrl
    actually applies the mount prefix. If one were made external, resourceUrl
    would (correctly) leave it alone and the pin above would be a false comfort."""
    hrefs = [
        re.search(r"href:\s*'([^']+)'", item).group(1)
        for item in _navigation_items()
        if "classic: true" in item
    ]
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
