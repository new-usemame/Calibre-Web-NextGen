# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The cover download identifies the software the same way the validator
probe does. Measured before the fix: upload.wikimedia.org answered 403 to
the bare python-requests agent and 200 to an identifying one — the preview
and the save path must send the same headers or a link that previewed
fine fails on apply."""

from __future__ import annotations

import pytest
import requests

from cps.services.cover_url_validator import cover_fetch_headers


@pytest.fixture
def helper(monkeypatch):
    import cps.helper as helper
    monkeypatch.setattr(helper, "use_advocate", True)
    monkeypatch.setattr(helper.cli_param, "allow_localhost", False)
    return helper


def _refusing_response(seen):
    """Records the request headers and refuses, so the helper returns
    before it touches the filesystem."""
    def fake_get(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        seen["stream"] = kwargs.get("stream")

        def raise_for_status():
            raise requests.HTTPError("403 Forbidden")
        return type("Resp", (), {"status_code": 403, "headers": {},
                                 "raise_for_status": staticmethod(raise_for_status),
                                 "close": staticmethod(lambda: None)})()
    return fake_get


def test_save_cover_from_url_sends_the_identifying_headers(helper, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(helper.cw_advocate, "get", _refusing_response(seen))
    ok, _message = helper.save_cover_from_url("https://upload.example.test/cover.png", str(tmp_path))
    assert ok is False
    assert seen["headers"] == cover_fetch_headers()
    assert seen["headers"]["User-Agent"].startswith(helper.constants.USER_AGENT)
    assert seen["headers"]["User-Agent"].endswith("(+https://github.com/new-usemame/calibre-web-nextgen)")


def test_personal_cover_url_sends_the_identifying_headers(monkeypatch):
    from cps.services import user_cover
    from datetime import datetime, timezone
    seen = {}
    monkeypatch.setattr(user_cover.cw_advocate, "get", _refusing_response(seen))
    staged, _message = user_cover.stage_url(1, 1, datetime.now(timezone.utc), "https://upload.example.test/cover.png")
    assert staged is None
    assert seen["headers"] == cover_fetch_headers()
