# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the Docker integration client's login readiness."""

from types import SimpleNamespace

import pytest

from tests.conftest import (
    _authenticate_cwa_session,
    check_container_available,
)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Session:
    def __init__(self, login_pages, login_status=302, whoami_status=200):
        self.login_pages = list(login_pages)
        self.login_status = login_status
        self.whoami_status = whoami_status
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/api/v1/me"):
            return SimpleNamespace(status_code=self.whoami_status, text="")
        page = (
            self.login_pages.pop(0)
            if len(self.login_pages) > 1
            else self.login_pages[0]
        )
        return SimpleNamespace(status_code=200, text=page)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return SimpleNamespace(status_code=self.login_status, text="")


def _authenticate(session, clock, wait_seconds=1):
    return _authenticate_cwa_session(
        session,
        "http://cwa.test",
        wait_seconds=wait_seconds,
        poll_interval=0.5,
        clock=clock,
        sleep=clock.sleep,
    )


def test_tokenless_login_page_then_token_proceeds_with_one_post():
    session = _Session(
        [
            "<html>app is still starting</html>",
            '<form><input name="csrf_token" value="ready-token"></form>',
        ]
    )
    clock = _Clock()

    _authenticate(session, clock)

    post_calls = [call for call in session.calls if call[0] == "POST"]
    assert len(post_calls) == 1
    assert post_calls[0][2]["data"] == {
        "username": "admin",
        "password": "admin123",
        "csrf_token": "ready-token",
    }
    assert session.calls[-1][1].endswith("/api/v1/me")


def test_tokenless_login_page_for_whole_bound_reports_readiness_not_credentials():
    page = "still-starting:" + ("x" * 240)
    session = _Session([page])
    clock = _Clock()

    with pytest.raises(pytest.fail.Exception) as exc_info:
        _authenticate(session, clock)

    message = str(exc_info.value)
    assert message == (
        "no csrf token on /login after 1 s "
        f"(page: {page[:200]!r})"
    )
    assert "credentials rejected" not in message
    assert not any(call[0] == "POST" for call in session.calls)


@pytest.mark.parametrize("status_code", [401, 403])
def test_login_rejection_reports_credentials_and_http_status(status_code):
    session = _Session(
        ['<input name="csrf_token" value="ready-token">'],
        login_status=status_code,
    )
    clock = _Clock()

    with pytest.raises(pytest.fail.Exception) as exc_info:
        _authenticate(session, clock)

    assert str(exc_info.value) == (
        f"CWA container credentials rejected (HTTP {status_code})"
    )
    assert sum(call[0] == "POST" for call in session.calls) == 1


@pytest.mark.parametrize("status_code", [401, 403])
def test_redirect_without_authenticated_session_reports_credentials(status_code):
    session = _Session(
        ['<input name="csrf_token" value="ready-token">'],
        login_status=302,
        whoami_status=status_code,
    )
    clock = _Clock()

    with pytest.raises(pytest.fail.Exception) as exc_info:
        _authenticate(session, clock)

    assert str(exc_info.value) == (
        f"CWA container credentials rejected (HTTP {status_code}); "
        "login POST returned HTTP 302 but /api/v1/me did not authenticate "
        "the session"
    )
    assert sum(call[0] == "POST" for call in session.calls) == 1


def test_container_availability_uses_the_image_health_contract(monkeypatch):
    seen = []

    def fake_get(url, **kwargs):
        seen.append((url, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("tests.conftest.requests.get", fake_get)

    assert check_container_available("9876") is True
    assert seen == [("http://localhost:9876/health", {"timeout": 2})]
