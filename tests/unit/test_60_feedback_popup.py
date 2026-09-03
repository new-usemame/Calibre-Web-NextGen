# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for fork #60 — the new-UI fallback feedback popup.

When the SPA shell detects a browser that cannot run it, its capability fallback
navigates to Classic with ?cwng_feedback=newui; the classic layout includes a
partial that shows a short, two-step, fully anonymous feedback prompt.

The load-bearing invariant is ANONYMITY ("unmarked mail"): the popup must POST
only { type, reasons, comment } and must never attach identity — no username,
account id, email, IP, cookies/credentials, or device info. These tests pin that
so a future edit can't silently start leaking identity, plus the wiring (layout
include, gate, SPA entry point, disclosure copy) that makes the flow work.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
LAYOUT_HTML = REPO_ROOT / "cps" / "templates" / "layout.html"
PARTIAL = REPO_ROOT / "cps" / "templates" / "cwng_feedback_popup.html"
FEEDBACK_JS = REPO_ROOT / "cps" / "static" / "js" / "cwng-feedback.js"
SPA_PY = REPO_ROOT / "cps" / "spa.py"
WEB_PY = REPO_ROOT / "cps" / "web.py"

WORKER_HOST = "https://app.calibrewebnextgen.com"
WORKER_ENDPOINT = WORKER_HOST + "/feedback"
FEEDBACK_TYPE = "new_version_feedback"


# ── files exist ──────────────────────────────────────────────────────────────
def test_partial_and_js_exist():
    assert PARTIAL.is_file(), "cps/templates/cwng_feedback_popup.html must exist"
    assert FEEDBACK_JS.is_file(), "cps/static/js/cwng-feedback.js must exist"


# ── layout wiring: included, and behind the SPA gate ─────────────────────────
def test_layout_includes_partial_inside_spa_gate():
    """The popup is included only when the SPA is available (there's a new UI to
    switch back from) — i.e. inside the existing `cwng_spa_enabled` block, not at
    top level where it would render on SPA-disabled instances too."""
    src = LAYOUT_HTML.read_text()
    assert "cwng_feedback_popup.html" in src, "layout.html must include the popup partial"
    # The include must sit within a cwng_spa_enabled conditional block.
    idx = src.index("cwng_feedback_popup.html")
    preceding = src[:idx]
    gate = preceding.rfind("cwng_spa_enabled")
    endif = preceding.rfind("{% endif %}")
    assert gate != -1 and gate > endif, (
        "the feedback partial include must be inside a `cwng_spa_enabled` block"
    )


# ── ANONYMITY invariant (the load-bearing guarantee) ─────────────────────────
def test_js_posts_only_anonymous_fields():
    js = FEEDBACK_JS.read_text()
    # The single JSON.stringify that builds the request body must contain exactly
    # type / reasons / comment and nothing identity-bearing.
    m = re.search(r"JSON\.stringify\(\s*\{([^}]*)\}\s*\)", js)
    assert m, "expected a JSON.stringify({...}) building the POST body"
    body = m.group(1)
    for allowed in ("type", "reasons", "comment"):
        assert allowed in body, f"payload should include {allowed!r}"
    for forbidden in ("username", "user_id", "userid", "email", "cookie", "ip",
                      "device", "session", "token", "name:"):
        assert forbidden not in body.lower(), (
            f"payload must not carry identity field {forbidden!r} — anonymity invariant"
        )


def test_js_never_sends_credentials_cross_origin():
    """Cookies/credentials must never ride along on the cross-origin POST, or the
    'no account/IP is sent' promise would be false."""
    js = FEEDBACK_JS.read_text()
    assert re.search(r'credentials\s*:\s*["\']omit["\']', js), (
        'the fetch() must set credentials: "omit"'
    )


def test_partial_advertises_correct_endpoint_and_type():
    src = PARTIAL.read_text()
    assert WORKER_ENDPOINT in src, f"partial must POST to {WORKER_ENDPOINT}"
    assert FEEDBACK_TYPE in src, f"feedback type must be {FEEDBACK_TYPE!r}"


# ── UX contract: two steps, anon-by-default, honest disclosure ───────────────
def test_anonymize_checkbox_checked_by_default():
    src = PARTIAL.read_text()
    m = re.search(r'id="cwng-fb-anon"[^>]*', src)
    assert m and "checked" in m.group(0), (
        "the 'Anonymize my feedback' checkbox must be checked by default"
    )


def test_disclosure_copy_is_honest_about_ip_edge():
    src = PARTIAL.read_text()
    # The honest residual: the edge briefly sees the IP for spam control only.
    assert "No account, name, IP" in src
    assert "one-way hash" in src
    assert "never store" in src


def test_two_step_flow_present():
    src = PARTIAL.read_text()
    for step in ("reasons", "comment", "done", "error"):
        assert f'data-cwng-fb-step="{step}"' in src, f"missing popup step {step!r}"
    # At least the four canonical reasons the design specced (+ any extras).
    for reason in ("bug", "look", "missing", "different"):
        assert f'value="{reason}"' in src, f"missing reason checkbox {reason!r}"


# ── the one-shot marker is what shows/strips the popup ───────────────────────
def test_error_close_does_not_suppress_retry():
    """A failed send must NOT mark the version answered — otherwise a transient
    Worker/network failure permanently hides the prompt though no feedback was
    sent. close() guards markAnswered() on the error panel being visible."""
    js = FEEDBACK_JS.read_text()
    assert "panels.error" in js and "errored" in js, (
        "close() must skip markAnswered() while the error panel is showing"
    )


def test_js_gates_on_marker_and_strips_it():
    js = FEEDBACK_JS.read_text()
    assert "cwng_feedback" in js, "JS must read the ?cwng_feedback marker"
    assert "replaceState" in js, "JS must strip the marker from the URL after showing"


# ── SPA capability-fallback entry point ─────────────────────────────────────
def test_spa_shell_keeps_classic_capability_fallback():
    src = SPA_PY.read_text()
    assert "<noscript><meta" in src
    assert "<script nomodule>" in src
    assert "cwng_feedback=newui" in src


# ── CSP must permit the cross-origin POST (regression: without connect-src the ─
#    browser blocks the fetch and no feedback ever reaches the server) ──────────
def test_csp_allows_feedback_endpoint():
    src = WEB_PY.read_text()
    assert "connect-src" in src, (
        "add_security_headers must emit a connect-src directive or the browser "
        "blocks the feedback POST (falls back to default-src 'self')"
    )
    assert WORKER_HOST in src, (
        "the feedback endpoint host (origin) must be allowlisted in connect-src"
    )
    # connect-src must still carry 'self' so existing same-origin XHR isn't broken
    # by newly specifying the directive (which replaces the default-src fallback).
    start = src.index("connect_src")
    emit = src.index('"; connect-src', start)  # the csp += line that emits it
    block = src[start:emit]
    assert "'self'" in block, "connect-src must include 'self'"
    assert WORKER_HOST in block, "connect-src must include the feedback host origin"


# ── real Jinja render (not just source-pin) ──────────────────────────────────
def test_partial_renders_and_produces_overlay():
    """Render the partial the way Flask would, with url_for/_/version provided, and
    assert the resulting HTML carries the dialog + endpoint + version data attrs."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(PARTIAL.parent)),
        autoescape=True,
    )
    env.globals["_"] = lambda s: s  # gettext passthrough
    env.globals["url_for"] = lambda endpoint, **kw: "/static/js/cwng-feedback.js"
    html = env.get_template("cwng_feedback_popup.html").render(cwng_app_version="9.9.9-test")
    assert 'id="cwng-fb-overlay"' in html
    assert 'role="dialog"' in html and 'aria-modal="true"' in html
    assert WORKER_ENDPOINT in html
    assert 'data-version="9.9.9-test"' in html
    assert "Before you go" in html
    # It ships hidden — the JS reveals it only on the marker.
    assert re.search(r'id="cwng-fb-overlay"[^>]*\shidden', html)
