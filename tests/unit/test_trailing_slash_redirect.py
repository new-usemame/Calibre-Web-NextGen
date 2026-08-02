# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioral tests for fork #628 — a trailing slash should not 404.

Reporter @iroQuai: "when i go to sub.domain.com/kosync/ i get an 404. If i
remove the last / I do get to the right page."

Werkzeug treats ``/kosync`` and ``/kosync/`` as different resources. Almost
every route in this app is declared without a trailing slash, so any page
reached from the address bar or a bookmark with one 404s. The fix applies a
single policy in ``cps/url_policy.py``, invoked from the app's 404 handler,
so no route needs hand-annotating.

These tests are behavioural: they build real ``werkzeug`` route maps and real
Flask apps and assert on status codes and ``Location`` headers. The one
source-level check (``test_error_http_is_wired_to_the_policy``) is an AST
assertion that the handler is actually wired up, because the behaviour above
is only reachable through it.
"""

import ast
import importlib.util
from pathlib import Path

import pytest
from flask import Flask, abort
from werkzeug.routing import Map, Rule


REPO_ROOT = Path(__file__).resolve().parents[2]
URL_POLICY_PATH = REPO_ROOT / "cps" / "url_policy.py"
ERROR_HANDLER_PATH = REPO_ROOT / "cps" / "error_handler.py"


def _load_url_policy():
    """Import cps/url_policy.py without initialising the whole cps package."""
    spec = importlib.util.spec_from_file_location("_cwng_url_policy", URL_POLICY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


url_policy = _load_url_policy()


# --------------------------------------------------------------------------
# canonical_slashless_path — pure routing logic, no Flask app needed
# --------------------------------------------------------------------------

def _adapter():
    """A route map shaped like the real app's: slash-less rules, one legacy
    branch rule, and one POST-only endpoint."""
    return Map([
        Rule("/kosync", endpoint="kosync_page"),
        Rule("/admin/view", endpoint="admin_view"),
        Rule("/branch/", endpoint="branch"),
        Rule("/only-post", endpoint="only_post", methods=["POST"]),
    ]).bind("example.org")


def test_trailing_slash_on_a_real_page_resolves_to_the_slashless_path():
    """The reported bug: /kosync/ must map back to /kosync."""
    assert url_policy.canonical_slashless_path(_adapter(), "/kosync/", "GET") == "/kosync"


def test_multi_segment_path_keeps_its_prefix():
    assert (
        url_policy.canonical_slashless_path(_adapter(), "/admin/view/", "GET")
        == "/admin/view"
    )


def test_repeated_trailing_slashes_collapse():
    assert url_policy.canonical_slashless_path(_adapter(), "/kosync//", "GET") == "/kosync"


def test_path_without_a_trailing_slash_is_left_alone():
    assert url_policy.canonical_slashless_path(_adapter(), "/kosync", "GET") is None


def test_root_is_never_stripped():
    """"/" must not be rewritten to "" — that would break the library index."""
    assert url_policy.canonical_slashless_path(_adapter(), "/", "GET") is None


def test_unknown_path_still_has_no_target():
    """A genuine 404 stays a 404; we only rescue real routes."""
    assert url_policy.canonical_slashless_path(_adapter(), "/no-such-page/", "GET") is None


def test_rule_declared_with_a_trailing_slash_is_left_to_werkzeug():
    """Werkzeug already redirects /branch -> /branch/ for branch rules. We must
    not fight that by rewriting in the opposite direction."""
    assert url_policy.canonical_slashless_path(_adapter(), "/branch/", "GET") is None


def test_method_mismatch_is_not_rescued():
    """/only-post/ over GET resolves for no method we can serve, so the caller
    keeps its original error rather than being bounced to a 405."""
    assert url_policy.canonical_slashless_path(_adapter(), "/only-post/", "GET") is None


def test_method_match_is_rescued():
    assert (
        url_policy.canonical_slashless_path(_adapter(), "/only-post/", "POST")
        == "/only-post"
    )


# --------------------------------------------------------------------------
# open-redirect hardening
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "//evil.example/",
    "/\\evil.example/",
    "//evil.example/kosync/",
    "/kosync\\@evil.example/",
    "/kosync\r\nSet-Cookie: x=1/",
])
def test_never_redirects_off_host(hostile):
    """The target is built from the request line, so a path a browser could
    read as absolute, protocol-relative, or header-splitting must never become
    a Location."""
    assert url_policy.canonical_slashless_path(_adapter(), hostile, "GET") is None


def test_leading_slashes_cannot_produce_a_protocol_relative_target():
    """"////kosync/" collapses to exactly one leading slash, never two."""
    result = url_policy.canonical_slashless_path(_adapter(), "////kosync/", "GET")
    assert result is None or not result.startswith("//")


# --------------------------------------------------------------------------
# trailing_slash_redirect_url — end to end through a real Flask app
# --------------------------------------------------------------------------

def _app():
    """A Flask app wired the way cps/error_handler.py wires the real one."""
    app = Flask(__name__)

    @app.route("/kosync")
    def kosync_page():
        return "kosync setup"

    @app.route("/only-post", methods=["POST"])
    def only_post():
        return "posted"

    @app.route("/gone")
    def gone():
        abort(404)

    @app.errorhandler(404)
    def not_found(error):
        from flask import redirect, request

        if request.url_rule is None:
            target = url_policy.trailing_slash_redirect_url()
            if target:
                return redirect(target, code=308)
        return "not found", 404

    return app


def test_get_with_trailing_slash_redirects_to_the_page():
    response = _app().test_client().get("/kosync/")
    assert response.status_code == 308
    assert response.headers["Location"].endswith("/kosync")


def test_redirect_preserves_the_query_string():
    response = _app().test_client().get("/kosync/?page=2&sort=new")
    assert response.status_code == 308
    assert response.headers["Location"].endswith("/kosync?page=2&sort=new")


def test_redirect_preserves_a_reverse_proxy_subpath():
    """Behind `PROXY_SCRIPT_NAME=/cwa` the target must stay under /cwa, or the
    redirect walks the user out of the mount and 404s again."""
    response = _app().test_client().get(
        "/kosync/", environ_overrides={"SCRIPT_NAME": "/cwa"}
    )
    assert response.status_code == 308
    assert response.headers["Location"].endswith("/cwa/kosync")


def test_redirect_is_method_preserving_for_post():
    """308, not 301/302 — a POST must not be silently downgraded to a GET."""
    response = _app().test_client().post("/only-post/")
    assert response.status_code == 308
    assert response.headers["Location"].endswith("/only-post")


def test_abort_404_from_inside_a_view_is_not_redirected():
    """A view that aborts 404 ("no such book") has a matched url_rule, so the
    policy must not hijack it into a redirect loop."""
    response = _app().test_client().get("/gone")
    assert response.status_code == 404


def test_unknown_path_with_trailing_slash_still_404s():
    response = _app().test_client().get("/no-such-page/")
    assert response.status_code == 404


def test_following_the_redirect_reaches_the_page():
    """The whole point, end to end: the user typed the slash and still lands."""
    response = _app().test_client().get("/kosync/", follow_redirects=True)
    assert response.status_code == 200
    assert b"kosync setup" in response.data


# --------------------------------------------------------------------------
# wiring — the behaviour above is only reachable if error_http calls the policy
# --------------------------------------------------------------------------

def _error_http_ast():
    tree = ast.parse(ERROR_HANDLER_PATH.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "error_http":
            return node
    raise AssertionError("cps/error_handler.py no longer defines error_http")


def test_error_http_is_wired_to_the_policy():
    node = _error_http_ast()
    called = {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "trailing_slash_redirect_url" in called, (
        "cps/error_handler.py::error_http must consult the trailing-slash "
        "policy, or /kosync/ 404s again (fork #628)."
    )
    assert "redirect" in called, "error_http must issue the redirect it computes"


def test_error_http_guards_on_404_and_unmatched_rule():
    """Without both guards the policy would run for 401/403 pages and for
    abort(404) calls from inside views."""
    node = _error_http_ast()
    for branch in ast.walk(node):
        if not isinstance(branch, ast.If):
            continue
        test_src = ast.unparse(branch.test)
        if "error.code == 404" not in test_src:
            continue
        body_src = ast.unparse(ast.Module(body=branch.body, type_ignores=[]))
        assert "request.url_rule is None" in test_src, (
            "the 404 branch must also require an unmatched rule, or abort(404) "
            "from inside a view gets hijacked"
        )
        assert "trailing_slash_redirect_url" in body_src
        return
    raise AssertionError(
        "error_http has no `error.code == 404` branch guarding the "
        "trailing-slash policy (fork #628)"
    )
