# SPDX-License-Identifier: GPL-3.0-or-later
"""The download-start cookie that lets a page show "Preparing... -> Downloaded".

The page makes up a random token, requests the normal download URL with
``?dl_token=<token>`` and watches for ``cwng_pack_token``. The cookie appears
only on a successful download, so "started", "busy" and "failed" are told apart
without holding the file in memory (a blob: save is blocked by the CSP in
Firefox, #2710).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cps import debug_info, spa

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, status_code=200):
        self.cookies = []
        self.status_code = status_code

    def set_cookie(self, name, value, **kwargs):
        self.cookies.append((name, value, kwargs))


@pytest.fixture
def served(monkeypatch):
    """Run an endpoint with a fake request and a recording response."""
    state = {"response": None}

    def run(endpoint, args, secure=False, busy=False, method="GET", status=200, script_root=""):
        monkeypatch.setattr(debug_info, "has_request_context", lambda: True)
        monkeypatch.setattr(debug_info, "request", SimpleNamespace(args=args, is_secure=secure, method=method))
        # The mount prefix is read (and sanitised) by spa, which has its own request.
        monkeypatch.setattr(spa, "request", SimpleNamespace(script_root=script_root))
        if busy:
            def raise_busy(_sanitize):
                raise debug_info.PackBusy()
            monkeypatch.setattr(debug_info, "_get_pack", raise_busy)
        else:
            monkeypatch.setattr(debug_info, "_get_pack", lambda _sanitize: b"zip")
        state["response"] = _Response(status)
        monkeypatch.setattr(debug_info, "send_file", lambda *a, **kw: state["response"])
        return getattr(debug_info, endpoint)()

    return run, state


TOKEN = "k3Jx9_ZpQ-w81aBc"


class TestTokenValidation:
    @pytest.mark.parametrize("value", [TOKEN, "A" * 8, "a" * 64, "ab_-CD12"])
    def test_well_formed_tokens_are_accepted(self, monkeypatch, value):
        monkeypatch.setattr(debug_info, "has_request_context", lambda: True)
        monkeypatch.setattr(debug_info, "request", SimpleNamespace(args={"dl_token": value}, is_secure=False))
        assert debug_info._requested_token() == value

    @pytest.mark.parametrize("value", ["", "short", "a" * 65, "has space1", "semi;colon1", "new\nline12",
                                       "quo\"te1234", "path/../1234", "<script>1", "ünïcode12"])
    def test_anything_else_is_ignored(self, monkeypatch, value):
        monkeypatch.setattr(debug_info, "has_request_context", lambda: True)
        monkeypatch.setattr(debug_info, "request", SimpleNamespace(args={"dl_token": value}, is_secure=False))
        assert debug_info._requested_token() is None

    def test_missing_param_and_missing_request_context(self, monkeypatch):
        monkeypatch.setattr(debug_info, "has_request_context", lambda: True)
        monkeypatch.setattr(debug_info, "request", SimpleNamespace(args={}, is_secure=False))
        assert debug_info._requested_token() is None
        monkeypatch.setattr(debug_info, "has_request_context", lambda: False)
        assert debug_info._requested_token() is None


@pytest.mark.parametrize("endpoint", ["send_debug_sanitized", "send_debug"])
class TestCookieOnDownload:
    def test_success_sets_the_token_cookie_readable_by_the_page(self, served, endpoint):
        run, state = served
        assert run(endpoint, {"dl_token": TOKEN}) is state["response"]
        (name, value, kwargs), = state["response"].cookies
        assert (name, value) == (debug_info.PACK_TOKEN_COOKIE, TOKEN)
        assert kwargs["max_age"] == debug_info.PACK_TOKEN_COOKIE_SECONDS
        assert kwargs["path"] == "/" and kwargs["samesite"] == "Lax" and kwargs["secure"] is False
        assert "httponly" not in kwargs  # the page's script has to read it

    def test_cookie_is_secure_over_https(self, served, endpoint):
        run, state = served
        run(endpoint, {"dl_token": TOKEN}, secure=True)
        assert state["response"].cookies[0][2]["secure"] is True

    def test_no_token_means_no_cookie_and_the_download_still_works(self, served, endpoint):
        run, state = served
        assert run(endpoint, {}) is state["response"]
        assert state["response"].cookies == []

    def test_a_malformed_token_gets_no_cookie(self, served, endpoint):
        run, state = served
        assert run(endpoint, {"dl_token": "bad token;"}) is state["response"]
        assert state["response"].cookies == []

    def test_busy_never_sets_the_cookie(self, served, endpoint):
        # The page must see "no cookie" and read the 429, not report a download.
        run, state = served
        _body, status, _headers = run(endpoint, {"dl_token": TOKEN}, busy=True)
        assert status == 429 and state["response"].cookies == []

    def test_a_head_request_gets_no_cookie(self, served, endpoint):
        # HEAD answers with the headers and no file: nothing has started downloading.
        run, state = served
        assert run(endpoint, {"dl_token": TOKEN}, method="HEAD") is state["response"]
        assert state["response"].cookies == []

    @pytest.mark.parametrize("status", [206, 304, 416])
    def test_only_the_attachment_itself_gets_the_cookie(self, served, endpoint, status):
        run, state = served
        run(endpoint, {"dl_token": TOKEN}, status=status)
        assert state["response"].cookies == []

    @pytest.mark.parametrize("script_root, expected", [
        ("", "/"),
        ("/calibre", "/calibre"),
        ("/books/cwng", "/books/cwng"),
        ("/calibre/", "/calibre"),
    ])
    def test_the_cookie_is_scoped_to_the_mount_path_not_the_root(self, served, endpoint, script_root, expected):
        run, state = served
        run(endpoint, {"dl_token": TOKEN}, script_root=script_root)
        assert state["response"].cookies[0][2]["path"] == expected

    @pytest.mark.parametrize("script_root", ["//evil.example", "/a b", "/x;path=/", "/../up", "calibre"])
    def test_an_unexpected_prefix_falls_back_to_the_root_path(self, served, endpoint, script_root):
        # The prefix is request-derived (X-Script-Name / X-Forwarded-Prefix) and goes
        # into a Set-Cookie header, so it is sanitised the way the SPA shell's is.
        run, state = served
        run(endpoint, {"dl_token": TOKEN}, script_root=script_root)
        assert state["response"].cookies[0][2]["path"] == "/"
