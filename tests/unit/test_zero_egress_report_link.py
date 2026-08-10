# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Zero-egress guarantees for the precomposed classic-UI report link.

The feature exists because the operator killed telemetry outright: the app must
never phone home, and no IP or instance fact may ever reach us. The report link
is the replacement — the app composes text, the USER decides whether to post it.

These tests pin the properties that make that true, because every one of them
is the kind that can be quietly undone by a reasonable-looking edit ("just
include the traceback, it's more useful").
"""
import re
from urllib.parse import urlparse, parse_qs

import pytest

from cps import report_link


class _Rule:
    def __init__(self, rule):
        self.rule = rule


class _Req:
    def __init__(self, url_rule):
        self.url_rule = url_rule


@pytest.fixture
def matched_route(monkeypatch):
    """Pretend a Flask rule matched, without standing up an app context."""
    def _set(rule):
        monkeypatch.setattr(report_link, "request", _Req(_Rule(rule) if rule else None))
    return _set


def _body_of(url):
    return parse_qs(urlparse(url).query)["body"][0]


def _code_only():
    """The module's executable source, with docstrings and comments stripped.

    Necessary, not fussy: this module DISCUSSES the fields it must never send,
    by name, so that a later reader knows why they are absent. A source scan
    that matched the prose would be permanently red — the classic gate that
    cries wolf until somebody deletes it.
    """
    import inspect

    source = inspect.getsource(report_link)
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    return re.sub(r"^\s*#.*$", "", source, flags=re.M)


# ── The load-bearing guarantee ───────────────────────────────────────────────

def test_module_never_transmits():
    """The module must contain no network call at all.

    Asserted mechanically rather than by review: re-adding a POST here would
    reinstate exactly the telemetry this replaced, and would look entirely
    reasonable in a diff ("report 500s automatically so we hear about them").
    """
    code = _code_only()

    for forbidden in ("requests.", "urlopen", "urlretrieve", "http.client",
                      "socket.", "session.post", "session.get", "aiohttp"):
        assert forbidden not in code, (
            "report_link must never transmit — found %r. It builds a URL string; "
            "only the user, clicking it, sends anything." % forbidden
        )


def test_url_targets_our_own_tracker(matched_route):
    matched_route("/book/<int:book_id>")
    assert report_link.build_issue_url("500 Internal Server Error").startswith(
        "https://github.com/new-usemame/Calibre-Web-NextGen/issues/new"
    )


# ── What must never appear in the body ───────────────────────────────────────

def test_body_carries_the_route_pattern_not_the_users_values(matched_route):
    """Flask's matched rule is a shape by construction: the dynamic parts are
    converter placeholders, so a book id or title slug cannot ride along."""
    matched_route("/book/<int:book_id>")
    body = _body_of(report_link.build_issue_url("500 Internal Server Error"))
    assert "/book/<int:book_id>" in body


def test_unmatched_route_is_not_echoed(matched_route):
    """On a 404 nothing matched, and request.path is user-supplied text. Echoing
    it into a prefilled PUBLIC issue body would be a reflection vector."""
    matched_route(None)
    body = _body_of(report_link.build_issue_url("404 Not Found"))
    assert "(no matched route)" in body


def test_body_contains_no_traceback_or_paths(matched_route):
    """The traceback is admin-gated in the template precisely because it carries
    filesystem paths; it must not be smuggled into a public issue body."""
    matched_route("/book/<int:book_id>")
    body = _body_of(report_link.build_issue_url("500 Internal Server Error"))
    assert "Traceback" not in body
    assert "/config" not in body
    assert "site-packages" not in body


def test_body_states_that_nothing_was_sent(matched_route):
    """The disclosure is load-bearing: the user is being asked to trust that
    landing on an error page did not already report it."""
    matched_route("/book/<int:book_id>")
    body = _body_of(report_link.build_issue_url("500 Internal Server Error"))
    assert re.search(r"nothing has been sent", body, re.I)


def test_library_title_is_never_included(matched_route, monkeypatch):
    """config_calibre_web_title is user-chosen ("Alex's Books") and reaches the
    template alongside this link, so it is exactly the field a future edit would
    reach for. It is not in the allowlist and must stay out."""
    matched_route("/book/<int:book_id>")
    url = report_link.build_issue_url("500 Internal Server Error")
    assert "config_calibre_web_title" not in _code_only()
    # The module never imports config at all, which is the structural version of
    # the same guarantee — it cannot include what it cannot reach.
    assert "import config" not in _code_only()
    assert "Books" not in _body_of(url)


# ── Robustness ───────────────────────────────────────────────────────────────

def test_url_stays_under_the_github_limit(matched_route):
    """An over-long URL makes GitHub serve an error page and the report is lost —
    a worse outcome than a slightly shorter report."""
    matched_route("/book/<int:book_id>")
    url = report_link.build_issue_url("500 Internal Server Error", "x" * 20000)
    assert len(url) <= 6000


def test_route_lookup_never_raises_outside_a_request(monkeypatch):
    """This runs on the error path. Raising here would replace the error page
    with a second error, so a missing request context degrades to a constant."""
    class _Boom:
        @property
        def url_rule(self):
            raise RuntimeError("working outside of request context")

    monkeypatch.setattr(report_link, "request", _Boom())
    assert report_link._route_pattern() == "(no matched route)"
