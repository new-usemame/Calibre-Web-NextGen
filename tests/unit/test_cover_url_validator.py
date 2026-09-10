# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit tests for the cover-URL validator service."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_validator_module():
    """Idempotently top up the cps stub so this test co-exists with
    sibling service tests."""
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "cps" / "services" / "cover_url_validator.py"

    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(repo_root / "cps")]
        sys.modules["cps"] = cps_pkg

    constants = sys.modules.get("cps.constants") or types.ModuleType("cps.constants")
    if not hasattr(constants, "USER_AGENT"):
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
    sys.modules["cps.constants"] = constants
    cps_pkg.constants = constants

    logger_mod = sys.modules.get("cps.logger") or types.ModuleType("cps.logger")
    if not hasattr(logger_mod, "create"):
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    advocate_mod = sys.modules.get("cps.cw_advocate") or types.ModuleType("cps.cw_advocate")
    if not hasattr(advocate_mod, "request"):
        advocate_mod.request = lambda *a, **k: None  # patched per-test
        advocate_mod.get = lambda *a, **k: None
    sys.modules["cps.cw_advocate"] = advocate_mod
    cps_pkg.cw_advocate = advocate_mod

    advocate_exc = sys.modules.get("cps.cw_advocate.exceptions")
    if advocate_exc is None or not hasattr(advocate_exc, "UnacceptableAddressException"):
        advocate_exc = types.ModuleType("cps.cw_advocate.exceptions")

        class UnacceptableAddressException(Exception):
            pass

        advocate_exc.UnacceptableAddressException = UnacceptableAddressException
        sys.modules["cps.cw_advocate.exceptions"] = advocate_exc
    advocate_mod.exceptions = advocate_exc

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(repo_root / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_url_validator", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_url_validator"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator_module()


def _mock_head(status, content_type, content_length):
    return types.SimpleNamespace(
        status_code=status,
        headers={"content-type": content_type, "content-length": str(content_length)},
    )


@pytest.mark.unit
class TestEarlyRejects:
    def test_empty_url_rejected(self):
        result = validator.validate_cover_url("")
        assert not result.valid
        assert result.error_code == "empty"

    def test_whitespace_url_rejected(self):
        assert validator.validate_cover_url("   ").error_code == "empty"

    def test_non_http_scheme_rejected(self):
        result = validator.validate_cover_url("ftp://example.com/cover.jpg")
        assert not result.valid
        assert result.error_code == "bad_scheme"

    def test_javascript_scheme_rejected(self):
        result = validator.validate_cover_url("javascript:alert(1)")
        assert not result.valid
        assert result.error_code == "bad_scheme"


@pytest.mark.unit
class TestSsrfGuard:
    def test_ssrf_blocked_returns_friendly_error(self):
        with patch.object(
            validator.cw_advocate, "request",
            side_effect=validator.UnacceptableAddressException("blocked"),
        ):
            result = validator.validate_cover_url("http://localhost:8080/cover.jpg")
        assert not result.valid
        assert result.error_code == "ssrf_blocked"
        assert "internal" in result.error_message.lower() or "local" in result.error_message.lower()


@pytest.mark.unit
class TestUnreachable:
    def test_request_exception_returns_unreachable(self):
        import requests as _r
        with patch.object(validator.cw_advocate, "request",
                          side_effect=_r.ConnectionError("nope")):
            result = validator.validate_cover_url("https://nope.example/x.jpg")
        assert not result.valid
        assert result.error_code == "unreachable"


@pytest.mark.unit
class TestStatusAndContentType:
    def test_404_rejected(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(404, "text/html", 1234)):
            result = validator.validate_cover_url("https://example.com/missing.jpg")
        assert not result.valid
        assert result.error_code == "bad_status"

    def test_html_content_type_rejected(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "text/html", 5000)):
            result = validator.validate_cover_url("https://example.com/page.html")
        assert not result.valid
        assert result.error_code == "not_image"

    def test_jpeg_content_type_passes_initial_check(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/jpeg", 250000)), \
             patch.object(validator, "_probe_dimensions", return_value=(975, 1500)):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        assert result.valid
        assert result.content_type == "image/jpeg"
        assert result.width == 975
        assert result.height == 1500

    def test_content_type_with_charset_suffix_normalized(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/png; charset=binary", 80000)), \
             patch.object(validator, "_probe_dimensions", return_value=(800, 1200)):
            result = validator.validate_cover_url("https://example.com/cover.png")
        assert result.valid
        assert result.content_type == "image/png"


@pytest.mark.unit
class TestSizeBounds:
    def test_too_small_rejected(self):
        # The 43-byte image/gif placeholder Amazon serves for unknown ASINs.
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/gif", 43)):
            result = validator.validate_cover_url("https://example.com/placeholder.gif")
        # Note: image/gif passes the content-type check, but the size filter
        # catches the placeholder.
        assert not result.valid
        assert result.error_code == "too_small"

    def test_too_large_rejected(self):
        # 200 MB JPEG — exceeds the 15 MB default.
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/jpeg", 200 * 1024 * 1024)):
            result = validator.validate_cover_url("https://example.com/huge.jpg")
        assert not result.valid
        assert result.error_code == "too_large"

    def test_missing_content_length_passes_size_check(self):
        # Some CDNs don't report content-length on HEAD. Don't reject for that;
        # the actual save-path will enforce the cap.
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/jpeg", 0)), \
             patch.object(validator, "_probe_dimensions", return_value=(900, 1200)):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        assert result.valid


@pytest.mark.unit
class TestSerialization:
    def test_to_dict_returns_jsonable_shape(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/jpeg", 250000)), \
             patch.object(validator, "_probe_dimensions", return_value=(975, 1500)):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        d = result.to_dict()
        assert d["valid"] is True
        assert d["width"] == 975
        assert d["height"] == 1500
        assert d["content_type"] == "image/jpeg"
        assert d["size_bytes"] == 250000


# ---------------------------------------------------------------------------
# Pasted-link fixes: an identifying User-Agent, a GET fallback when HEAD is
# refused, and Google Images results links unwrapped to the image behind them.
# Measured before the fix: upload.wikimedia.org answered 403 to the bare
# python-requests agent (HEAD and GET) and 200 to an identifying one, so a
# good link showed "Server returned HTTP 403." with the apply button disabled.
# ---------------------------------------------------------------------------

_IDENT_UA_TAIL = "(+https://github.com/new-usemame/calibre-web-nextgen)"


def _mock_get(status, content_type, body=b"", content_length=None):
    length = len(body) if content_length is None else content_length
    return types.SimpleNamespace(
        status_code=status,
        headers={"content-type": content_type, "content-length": str(length)},
        iter_content=lambda chunk_size=2048: iter([body]) if body else iter([]),
        close=lambda: None,
        raise_for_status=lambda: None,
    )


@pytest.mark.unit
class TestIdentifyingHeaders:
    def test_head_probe_identifies_the_software(self):
        seen = {}

        def fake_request(method, url, **kwargs):
            seen["headers"] = kwargs.get("headers") or {}
            return _mock_head(200, "image/jpeg", 250000)

        with patch.object(validator.cw_advocate, "request", side_effect=fake_request), \
             patch.object(validator, "_probe_dimensions", return_value=(900, 1200)):
            assert validator.validate_cover_url("https://example.com/cover.jpg").valid
        ua = seen["headers"].get("User-Agent", "")
        assert ua.startswith(validator.constants.USER_AGENT), ua
        assert ua.endswith(_IDENT_UA_TAIL), ua
        assert seen["headers"].get("Accept") == "image/*,*/*;q=0.8"

    def test_dimension_probe_get_identifies_the_software(self):
        seen = {}

        def fake_get(url, **kwargs):
            seen["headers"] = kwargs.get("headers") or {}
            return _mock_get(200, "image/jpeg", b"")

        with patch.object(validator.cw_advocate, "get", side_effect=fake_get):
            validator._probe_dimensions("https://example.com/cover.jpg")
        assert seen["headers"].get("User-Agent", "").endswith(_IDENT_UA_TAIL), seen


@pytest.mark.unit
class TestHeadFallback:
    def test_head_405_then_get_200_image_is_valid(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append(method)
            return _mock_head(405, "text/plain", 0)

        def fake_get(url, **kwargs):
            calls.append("GET")
            assert kwargs.get("stream") is True
            return _mock_get(200, "image/jpeg", b"\xff\xd8\xff", content_length=250000)

        with patch.object(validator.cw_advocate, "request", side_effect=fake_request), \
             patch.object(validator.cw_advocate, "get", side_effect=fake_get):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        assert result.valid, result
        assert result.content_type == "image/jpeg"
        assert result.size_bytes == 250000
        assert calls == ["HEAD", "GET"]

    def test_head_403_then_get_403_stays_bad_status(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(403, "text/html", 0)), \
             patch.object(validator.cw_advocate, "get",
                          return_value=_mock_get(403, "text/html", b"<html>")):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        assert not result.valid
        assert result.error_code == "bad_status"
        assert "403" in result.error_message

    def test_head_404_does_not_fall_back(self):
        get = patch.object(validator.cw_advocate, "get",
                           side_effect=AssertionError("GET must not run for a 404 HEAD"))
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(404, "text/html", 0)), get:
            result = validator.validate_cover_url("https://example.com/missing.jpg")
        assert result.error_code == "bad_status"

    def test_fallback_get_reads_at_most_the_probe_prefix(self):
        served = []

        def body_chunks(chunk_size=2048):
            for _ in range(100):
                served.append(chunk_size)
                yield b"\x00" * chunk_size

        resp = types.SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png", "content-length": "500000"},
            iter_content=body_chunks, close=lambda: None, raise_for_status=lambda: None,
        )
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(501, "", 0)), \
             patch.object(validator.cw_advocate, "get", return_value=resp):
            result = validator.validate_cover_url("https://example.com/cover.png")
        assert result.valid
        assert sum(served) <= validator._DIM_PROBE_BYTES + 2048


@pytest.mark.unit
class TestGoogleImgresLinks:
    IMGRES = ("https://www.google.com/imgres?imgurl=https%3A%2F%2Fcdn.example.test%2Fcovers%2F"
              "book%20one.jpg&imgrefurl=https%3A%2F%2Fexample.test%2Fbook&tbnid=abc")
    INNER = "https://cdn.example.test/covers/book one.jpg"

    def test_resolve_unwraps_imgurl(self):
        assert validator.resolve_pasted_cover_url(self.IMGRES) == self.INNER

    def test_resolve_accepts_country_and_images_hosts(self):
        for host in ("google.co.uk", "images.google.de", "www.google.com.au"):
            url = f"https://{host}/imgres?imgurl=https%3A%2F%2Fcdn.example.test%2Fa.jpg"
            assert validator.resolve_pasted_cover_url(url) == "https://cdn.example.test/a.jpg", host

    def test_resolve_ignores_non_google_hosts(self):
        for url in ("https://example.com/imgres?imgurl=https%3A%2F%2Fcdn.example.test%2Fa.jpg",
                    "https://google.com.evil.example/imgres?imgurl=https%3A%2F%2Fcdn.example.test%2Fa.jpg",
                    "https://www.google.com/search?imgurl=https%3A%2F%2Fcdn.example.test%2Fa.jpg"):
            assert validator.resolve_pasted_cover_url(url) == url, url

    def test_resolve_refuses_non_http_inner_values(self):
        for inner in ("javascript%3Aalert(1)", "file%3A%2F%2F%2Fetc%2Fpasswd", "cdn.example.test%2Fa.jpg", ""):
            url = f"https://www.google.com/imgres?imgurl={inner}&imgrefurl=x"
            assert validator.resolve_pasted_cover_url(url) == url, inner

    def test_validate_probes_the_inner_url_and_reports_resolved_url(self):
        probed = []

        def fake_request(method, url, **kwargs):
            probed.append(url)
            return _mock_head(200, "image/jpeg", 250000)

        with patch.object(validator.cw_advocate, "request", side_effect=fake_request), \
             patch.object(validator, "_probe_dimensions", return_value=(900, 1200)):
            result = validator.validate_cover_url(self.IMGRES)
        assert result.valid
        assert probed == [self.INNER]
        assert result.url == self.IMGRES          # the stale-response guard compares this
        assert result.resolved_url == self.INNER  # the client applies this one
        assert result.to_dict()["resolved_url"] == self.INNER

    def test_plain_url_has_no_resolved_url(self):
        with patch.object(validator.cw_advocate, "request",
                          return_value=_mock_head(200, "image/jpeg", 250000)), \
             patch.object(validator, "_probe_dimensions", return_value=(900, 1200)):
            result = validator.validate_cover_url("https://example.com/cover.jpg")
        assert result.resolved_url is None


@pytest.mark.unit
class TestOutcomeLogging:
    def _run(self, head, url="https://example.com/cover.jpg"):
        lines = []
        fake_log = types.SimpleNamespace(
            info=lambda msg, *args, **kw: lines.append(msg % args),
            debug=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None,
        )
        with patch.object(validator, "log", fake_log), \
             patch.object(validator.cw_advocate, "request", return_value=head), \
             patch.object(validator.cw_advocate, "get", return_value=_mock_get(403, "text/html")), \
             patch.object(validator, "_probe_dimensions", return_value=(1, 1)):
            validator.validate_cover_url(url)
        return lines

    def test_refusals_are_logged_at_info_with_the_outcome_code(self):
        lines = self._run(_mock_head(403, "text/html", 0))
        assert len(lines) == 1 and "bad_status 403" in lines[0] and "https://example.com/cover.jpg" in lines[0]
        lines = self._run(_mock_head(200, "text/html", 5000))
        assert len(lines) == 1 and "not_image text/html" in lines[0]

    def test_success_and_imgres_resolution_are_logged(self):
        lines = self._run(_mock_head(200, "image/jpeg", 250000))
        assert len(lines) == 1 and "outcome=valid" in lines[0]
        lines = self._run(_mock_head(200, "image/jpeg", 250000), url=TestGoogleImgresLinks.IMGRES)
        assert len(lines) == 1 and "resolved imgres" in lines[0]
