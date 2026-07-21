# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #1048 — the new UI could not run a duplicate scan.

Before the fix:
  * there was no /api/v1 endpoint that triggers a scan (the only trigger,
    POST /duplicates/trigger-scan, is on the legacy blueprint whose page the SPA
    route shadows),
  * the SPA's needs-scan empty state told admins to run it "from CWA settings",
    a page that has no scan control at all,
  * the admin panel's "Duplicate books" row linked to /duplicates — byte-for-byte
    the sidebar's destination — so the admin panel offered nothing new.

These tests pin all three, plus the auth gating and the delegate-don't-duplicate
contract on the new endpoint.
"""
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest

REPO = Path(__file__).resolve().parents[2]
SPA = REPO / "frontend" / "src"


def _ctx():
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context("/api/v1/duplicates/scan", method="POST")


def _user(anon=False, admin=True, edit=False):
    return SimpleNamespace(
        is_authenticated=not anon, is_anonymous=anon, id=1, name="maggie",
        role_admin=lambda: admin, role_edit=lambda: edit,
    )


def _body(resp):
    return json.loads(resp[0].get_data() if isinstance(resp, tuple) else resp.get_data())


# ── the endpoint exists and is wired to the api_v1 blueprint ──────────────────

@pytest.mark.unit
def test_scan_endpoint_is_registered_on_api_v1():
    from cps.api import duplicates as mod
    assert hasattr(mod, "trigger_duplicate_scan"), \
        "the SPA needs POST /api/v1/duplicates/scan to run a scan at all (#1048)"
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert re.search(r'@api_v1\.route\(\s*"/duplicates/scan",\s*methods=\["POST"\]\s*\)', src)


# ── auth gating ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_scan_anonymous_401():
    from cps.api import duplicates as mod
    with _ctx():
        with patch.object(mod, "current_user", _user(anon=True)):
            resp = inspect.unwrap(mod.trigger_duplicate_scan)()
    assert resp[1] == 401


@pytest.mark.unit
def test_scan_plain_user_403():
    from cps.api import duplicates as mod
    with _ctx():
        with patch.object(mod, "current_user", _user(admin=False, edit=False)):
            resp = inspect.unwrap(mod.trigger_duplicate_scan)()
    assert resp[1] == 403
    assert _body(resp)["error"]["code"] == "forbidden"


@pytest.mark.unit
@pytest.mark.parametrize("admin,edit", [(True, False), (False, True)])
def test_scan_admin_or_edit_reaches_the_legacy_trigger(admin, edit):
    """Both roles get through the guard and the legacy implementation is what runs."""
    from cps.api import duplicates as mod
    import cps.duplicates as legacy
    import cps.cwa_functions as cwaf
    sentinel = ({"success": True, "queued": True}, 200)
    with _ctx():
        with patch.object(mod, "current_user", _user(admin=admin, edit=edit)), \
             patch.object(cwaf, "_duplicate_full_scan_running", lambda: False), \
             patch.object(legacy, "trigger_scan", lambda **kw: sentinel):
            resp = inspect.unwrap(mod.trigger_duplicate_scan)()
    assert resp is sentinel


# ── delegate, don't duplicate ─────────────────────────────────────────────────

@pytest.mark.unit
def test_scan_endpoint_delegates_rather_than_reimplementing():
    """Queueing/cache-invalidation/fallback must stay in cps/duplicates.py.

    A copy here would drift: the legacy path also handles the sync fallback and
    auto-resolution. Pin that the API view imports the legacy view and calls it.
    """
    from cps.api import duplicates as mod
    src = inspect.getsource(mod.trigger_duplicate_scan)
    assert "from ..duplicates import trigger_scan" in src
    assert "return legacy_trigger_scan(allow_sync_fallback=False)" in src
    assert "TaskDuplicateScan" not in src, "scan queueing must not be re-implemented here"


# ── single-flight + no-freeze fallback (cross-family review findings) ─────────

@pytest.mark.unit
def test_second_scan_while_one_is_running_does_not_queue_another():
    """The SPA button re-enables the moment the POST returns, long before the
    scan finishes — without this guard, clicks stack full-library scans."""
    from cps.api import duplicates as mod
    import cps.duplicates as legacy
    import cps.cwa_functions as cwaf
    called = []
    with _ctx():
        with patch.object(mod, "current_user", _user()), \
             patch.object(cwaf, "_duplicate_full_scan_running", lambda: True), \
             patch.object(legacy, "trigger_scan", lambda **kw: called.append(kw)):
            resp = inspect.unwrap(mod.trigger_duplicate_scan)()
    body = _body(resp)
    assert called == [], "a second full scan must not be queued while one is running"
    assert body["already_running"] is True and body["queued"] is False
    assert body["success"] is True, "repeat clicks are idempotent, not an error"


@pytest.mark.unit
def test_scan_is_queued_when_none_is_running():
    from cps.api import duplicates as mod
    import cps.duplicates as legacy
    import cps.cwa_functions as cwaf
    seen = {}
    with _ctx():
        with patch.object(mod, "current_user", _user()), \
             patch.object(cwaf, "_duplicate_full_scan_running", lambda: False), \
             patch.object(legacy, "trigger_scan", lambda **kw: seen.update(kw) or ("ok", 200)):
            resp = inspect.unwrap(mod.trigger_duplicate_scan)()
    assert resp == ("ok", 200)
    assert seen == {"allow_sync_fallback": False}, \
        "the API path must opt out of the inline rebuild (gevent has no monkey-patch)"


@pytest.mark.unit
def test_legacy_trigger_keeps_the_sync_fallback_by_default():
    """The classic page's behaviour is unchanged — only the API opts out."""
    import cps.duplicates as legacy
    sig = inspect.signature(inspect.unwrap(legacy.trigger_scan))
    assert sig.parameters["allow_sync_fallback"].default is True


@pytest.mark.unit
def test_disabled_fallback_returns_503_instead_of_rebuilding_inline():
    """A worker failure must not turn one click into a whole-server freeze."""
    import cps.duplicates as legacy
    src = inspect.getsource(inspect.unwrap(legacy.trigger_scan))
    guard = src.index("if not allow_sync_fallback:")
    rebuild = src.index("rebuild_duplicate_index")
    assert guard < rebuild, "the opt-out must short-circuit before the inline rebuild"
    assert "503" in src[guard:rebuild]


@pytest.mark.unit
def test_scan_endpoint_is_not_csrf_exempt():
    """The SPA sends X-CSRFToken; this state-changing route keeps CSRF protection."""
    from cps.api import duplicates as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "csrf.exempt" not in src


# ── SPA surfaces (source pins — these are what the user actually sees) ────────

@pytest.mark.unit
def test_spa_duplicates_page_has_a_scan_button():
    src = (SPA / "pages" / "Duplicates.tsx").read_text(encoding="utf-8")
    assert "useTriggerDuplicateScan" in src
    assert "scan.mutate()" in src
    assert "Scan for duplicates" in src


@pytest.mark.unit
def test_spa_needs_scan_copy_no_longer_points_at_cwa_settings():
    """The old copy sent users to a settings page with no scan control."""
    src = (SPA / "pages" / "Duplicates.tsx").read_text(encoding="utf-8")
    assert "Run it from CWA settings" not in src
    assert "Use “Scan for duplicates” above to run it." in src


@pytest.mark.unit
def test_spa_scan_mutation_posts_to_the_api_endpoint():
    src = (SPA / "lib" / "queries.ts").read_text(encoding="utf-8")
    assert "export function useTriggerDuplicateScan" in src
    assert "apiPost('/api/v1/duplicates/scan')" in src
    assert re.search(r"useTriggerDuplicateScan[\s\S]{0,400}invalidateQueries\(\{ queryKey: \['duplicates'\] \}\)", src)


@pytest.mark.unit
def test_admin_panel_row_no_longer_duplicates_the_sidebar_link():
    """#1048's headline symptom: Admin → "Duplicate Books" landed on the same
    page as the sidebar entry. It now opens the detection settings instead."""
    src = (SPA / "pages" / "Admin.tsx").read_text(encoding="utf-8")
    assert "href: '/duplicates'" not in src
    assert "'/cwa-settings#duplicate-detection'" in src
    assert "Duplicate detection settings" in src


@pytest.mark.unit
def test_cwa_settings_template_has_the_deep_link_anchor():
    """The admin row's fragment must resolve to the duplicate-detection section."""
    html = (REPO / "cps" / "templates" / "cwa_settings.html").read_text(encoding="utf-8")
    idx = html.find('id="duplicate-detection"')
    assert idx != -1, "anchor target missing — the admin deep link would land at the top"
    assert "NextGen Duplicate Detection System" in html[idx:idx + 400]


@pytest.mark.unit
def test_new_spa_strings_are_anchored_for_extraction():
    """SPA-only msgids must live in cps/spa_strings.py or babel drops them."""
    anchored = (REPO / "cps" / "spa_strings.py").read_text(encoding="utf-8")
    for msgid in ("Scan for duplicates", "Starting scan…", "Duplicate detection settings",
                  "Could not start the duplicate scan."):
        assert msgid in anchored, f"{msgid!r} not anchored in cps/spa_strings.py"
