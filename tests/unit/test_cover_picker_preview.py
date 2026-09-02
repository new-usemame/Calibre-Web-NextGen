# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit + smoke tests for the e-reader cover preview endpoint (issue #84).

The endpoint at POST /book/<id>/cover/ereader-preview re-renders an image
through the existing cover_preview pipeline and returns a base64 data
URL. We test:

1. The pure helper render_preview_data_url() in
   cps.services.cover_preview (which exports pad_blob too).
2. Static structure of the Flask endpoint registered in
   cps.cover_picker (route present, function name, calls pad_blob,
   returns JSON).

Tests use the spec_from_file_location shim so they don't require full
cps package init.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_cps_stub():
    """Top up sys.modules with the minimal cps package stub. Idempotent
    so the test can co-exist with the other unit tests that share it."""
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
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

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg


def _load_cover_preview():
    _ensure_cps_stub()
    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_preview",
        REPO_ROOT / "cps" / "services" / "cover_preview.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_preview"] = module
    spec.loader.exec_module(module)
    return module


def _make_jpeg_bytes(width: int = 200, height: int = 300, color=(180, 60, 60)) -> bytes:
    """Build a real JPEG using Wand if present, falling back to a tiny
    static byte string (which the pad_blob will refuse and pass through).
    Tests that need real image processing must skip when Wand is missing."""
    try:
        from wand.image import Image
        from wand.color import Color
    except ImportError:
        pytest.skip("Wand/ImageMagick not available in this environment")

    color_str = "rgb(%d,%d,%d)" % color
    with Image(width=width, height=height, background=Color(color_str)) as img:
        img.format = "jpeg"
        return img.make_blob()


# --------------------------------------------------------------------- helper


class TestRenderPreviewDataUrl:
    """The new helper: takes JPEG bytes + settings, returns a base64 data URL."""

    def test_returns_data_url_prefixed_with_image_jpeg(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail(
                "cps.services.cover_preview.render_preview_data_url is not implemented yet"
            )

        jpeg = _make_jpeg_bytes()
        url = cover_preview.render_preview_data_url(
            jpeg,
            aspect="kobo_libra_color",
            fill_mode="edge_mirror",
            color="",
        )
        assert isinstance(url, str)
        assert url.startswith("data:image/jpeg;base64,")

    def test_decoded_payload_is_jpeg_magic_bytes(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")

        jpeg = _make_jpeg_bytes()
        url = cover_preview.render_preview_data_url(
            jpeg, aspect="kobo_libra_color", fill_mode="edge_mirror", color=""
        )
        b64 = url.split(",", 1)[1]
        decoded = base64.b64decode(b64)
        # JPEG SOI marker
        assert decoded[:2] == b"\xff\xd8"

    def test_padded_output_has_target_aspect_ratio(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")

        try:
            from wand.image import Image
        except ImportError:
            pytest.skip("Wand not available")

        # 2:3 publisher cover (200x300) padded to Libra Color 1264x1680 (3:4ish)
        jpeg = _make_jpeg_bytes(width=200, height=300)
        url = cover_preview.render_preview_data_url(
            jpeg, aspect="kobo_libra_color", fill_mode="edge_mirror", color=""
        )
        decoded = base64.b64decode(url.split(",", 1)[1])
        with Image(blob=decoded) as out:
            ratio = out.width / out.height
        # Libra Color = 1264/1680 = 0.7524; 2:3 = 0.6667. Padded should
        # land near 0.7524 (within rounding).
        assert abs(ratio - (1264 / 1680)) < 0.01

    def test_manual_color_is_honored(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")

        jpeg = _make_jpeg_bytes(width=200, height=300, color=(255, 255, 255))
        url = cover_preview.render_preview_data_url(
            jpeg,
            aspect="kobo_libra_color",
            fill_mode="manual",
            color="#cc7b19",
        )
        # Just verify it round-trips without error and returns a JPEG.
        assert url.startswith("data:image/jpeg;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded[:2] == b"\xff\xd8"

    def test_gradient_mode_renders_a_jpeg(self):
        # New "gradient" fill mode (operator-requested 2026-05-08): builds a
        # palette-matched top→bottom gradient on the pad area. Must round-trip
        # to a valid JPEG.
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")
        if "gradient" not in cover_preview.FILL_MODES:
            pytest.fail("'gradient' must be a registered fill mode")

        jpeg = _make_jpeg_bytes(width=200, height=300)
        url = cover_preview.render_preview_data_url(
            jpeg, aspect="kobo_libra_color", fill_mode="gradient", color="",
        )
        assert url.startswith("data:image/jpeg;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded[:2] == b"\xff\xd8"

    def test_gradient_mode_produces_target_aspect(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")
        try:
            from wand.image import Image
        except ImportError:
            pytest.skip("Wand not available")

        jpeg = _make_jpeg_bytes(width=200, height=300)
        url = cover_preview.render_preview_data_url(
            jpeg, aspect="kobo_libra_color", fill_mode="gradient", color="",
        )
        decoded = base64.b64decode(url.split(",", 1)[1])
        with Image(blob=decoded) as out:
            ratio = out.width / out.height
        assert abs(ratio - (1264 / 1680)) < 0.01

    def test_invalid_aspect_falls_back_to_default_or_raises_clear_error(self):
        cover_preview = _load_cover_preview()
        if not hasattr(cover_preview, "render_preview_data_url"):
            pytest.fail("helper not implemented")

        jpeg = _make_jpeg_bytes()
        # parse_target_ratio is documented to fall back to default for
        # bad input, so this should NOT raise — but the output should
        # still be a valid JPEG.
        url = cover_preview.render_preview_data_url(
            jpeg,
            aspect="not-a-real-aspect",
            fill_mode="edge_mirror",
            color="",
        )
        assert url.startswith("data:image/jpeg;base64,")


# --------------------------------------------------------------------- endpoint structure


class TestEreaderPreviewEndpointStructure:
    """Static-source checks that the Flask endpoint exists with the
    right shape. Exercising the route itself requires full app init,
    which the smoke layer doesn't take on; the e2e Playwright pass
    covers the wired-up flow."""

    BLUEPRINT_FILE = REPO_ROOT / "cps" / "cover_picker.py"

    def _read(self) -> str:
        return self.BLUEPRINT_FILE.read_text(encoding="utf-8")

    def test_endpoint_route_is_registered(self):
        src = self._read()
        assert (
            '@cover_picker.route("/book/<int:book_id>/cover/ereader-preview"' in src
            or "'/book/<int:book_id>/cover/ereader-preview'" in src
        ), "kobo-preview route missing from cover_picker blueprint"

    def test_endpoint_uses_post_method(self):
        src = self._read()
        # The route decorator should declare methods=["POST"]
        assert (
            'methods=["POST"]' in src or "methods=['POST']" in src
        ), "POST method should be declared"
        # And the kobo-preview route should be near a POST decorator
        kobo_idx = src.find("/book/<int:book_id>/cover/ereader-preview")
        assert kobo_idx != -1
        # Look back ~80 chars for a methods=POST marker on the same decorator
        window = src[max(0, kobo_idx - 200):kobo_idx + 200]
        assert "POST" in window, "kobo-preview route is not declared POST"

    def test_endpoint_function_name_matches_convention(self):
        src = self._read()
        assert "def cover_picker_ereader_preview(" in src, (
            "endpoint handler should be named cover_picker_ereader_preview "
            "to match the existing cover_picker_* naming pattern"
        )

    def test_endpoint_is_login_and_cover_source_gated(self):
        src = self._read()
        ereader_idx = src.find("def cover_picker_ereader_preview")
        assert ereader_idx != -1
        # Decorators sit above the function — look at the 400 chars before
        prefix = src[max(0, ereader_idx - 400):ereader_idx]
        assert "@user_login_required" in prefix, "endpoint must be auth-gated"
        assert "@cover_source_required" in prefix, (
            "endpoint must limit global sources to editors while allowing a "
            "signed-in user to preview their personal cover"
        )

    def test_endpoint_invokes_pad_blob_or_helper(self):
        src = self._read()
        # Must reach the padding pipeline, either via the helper we add
        # or pad_blob directly.
        assert (
            "render_preview_data_url" in src or "pad_blob" in src
        ), "endpoint must route through cover_preview"

    def test_endpoint_rejects_non_http_scheme(self):
        # Defense-in-depth: cw_advocate is the SSRF guard, but we don't
        # actively maintain it. Reject file:// / javascript: / data: / etc.
        # at the endpoint before they ever hit the fetch path.
        src = self._read()
        ereader_idx = src.find("def cover_picker_ereader_preview")
        block = src[ereader_idx:ereader_idx + 2500]
        assert (
            'scheme not in ("http", "https")' in block
            or "scheme not in ('http', 'https')" in block
        ), "endpoint must reject non-http(s) schemes server-side"
        assert "bad_scheme" in block, "should return a structured error code"

    def test_fetch_url_bytes_pre_streams_content_length(self):
        # Stop a 1 GB advertised-Content-Length attacker before we waste
        # a worker iterating chunks.
        src = self._read()
        helper_idx = src.find("def _fetch_url_bytes")
        block = src[helper_idx:helper_idx + 2500]
        assert (
            'resp.headers.get("Content-Length")' in block
            or "resp.headers.get('Content-Length')" in block
        ), "fetch helper must check Content-Length before streaming"
        assert "max_bytes" in block

    def test_picker_page_passes_config_to_template(self):
        # Regression caught during browser e2e: the picker GET handler must
        # explicitly pass config=config so cover_picker.html's
        # `{% if config.config_kobo_cover_padding_enabled %}` resolves. Without
        # that pass, Jinja silently sees `config` as Undefined and the Kobo
        # panel never renders even when the admin flag is on.
        src = self._read()
        page_idx = src.find("def cover_picker_page(")
        assert page_idx != -1
        render_idx = src.find("render_title_template(", page_idx)
        assert render_idx != -1, "picker page must call render_title_template"
        block = src[render_idx:render_idx + 800]
        assert (
            "config=config" in block
        ), "cover_picker_page must pass config=config (regression: silent panel-missing)"


# --------------------------------------------------------------------- JS module surface


class TestCoverPickerJsKoboPreview:
    """Smoke checks that the JS module wires up the toggle, debounces the
    candidate-grid MutationObserver, and (regression) refreshes previews
    immediately when aspect / fill_mode / color change while toggle is on."""

    JS_FILE = REPO_ROOT / "cps" / "static" / "js" / "cover_picker.js"

    def _read(self) -> str:
        return self.JS_FILE.read_text(encoding="utf-8")

    def test_js_has_kobo_setup_block(self):
        assert "setupEreaderPreview" in self._read()

    def test_js_caches_per_settings(self):
        # Settings-change refresh must hit cache for previously-fetched
        # combinations rather than re-firing a server burst on each toggle.
        src = self._read()
        assert "settingsKey" in src, "must compute a per-settings cache key"
        assert "perImg.has(key)" in src, "must check cache before fetching"

    def test_js_debounces_mutation_observer(self):
        # MutationObserver fires once per appended candidate card. We need
        # to coalesce a render burst into one refresh, not N parallel fetches.
        src = self._read()
        assert (
            "obsTimer" in src or "MutationObserver" in src and "setTimeout" in src
        ), "must debounce the grid MutationObserver"

    def test_js_handles_same_origin_cover_url(self):
        # cw_advocate refuses to fetch the host's own URL (SSRF guard). Same-
        # origin /cover/<id>/... must short-circuit to the server's disk-cover
        # path instead.
        src = self._read()
        assert (
            "isSameOriginCoverUrl" in src
        ), "must detect same-origin /cover/ URLs"

    def test_js_uses_abort_controller(self):
        # Toggle off / settings change must abort in-flight server work.
        # Otherwise the user pays for Wand cycles they no longer want.
        src = self._read()
        assert "AbortController" in src, "must wire up an AbortController"
        assert "abort()" in src, "must actually call abort somewhere"
        assert "signal" in src, "fetch must be passed the signal"

    def test_js_renders_loading_status(self):
        # User-visible feedback while a burst is processing — no more
        # 5 seconds of "did the toggle even work?"
        src = self._read()
        assert "cwa-cover-picker-ereader-status" in src, "must read the status pill element"
        assert "activeInFlight" in src, "must track the active in-flight count"

    def test_js_refresh_on_aspect_change(self):
        # Regression (operator-reported, 2026-05-08): switching the Target
        # Aspect Ratio dropdown with the toggle on must refire previews. The
        # change handler must call refreshAll (or trigger a refresh) — not
        # just sit on a cached older response.
        src = self._read()
        # Find the line that wires aspectSel change.
        idx = src.find('aspectSel.addEventListener("change"')
        assert idx != -1, "aspect dropdown must have a change listener"
        block = src[idx:idx + 200]
        assert (
            "refreshAll" in block or "refresh" in block.lower()
        ), "aspect change must drive a refresh"

    def test_js_refresh_on_fill_mode_change(self):
        # Regression: switching Border fill style must refire previews.
        src = self._read()
        # syncColorEnabled is one bound listener; the refresh wiring is the
        # other. Both must exist on fillSel change.
        # Count fillSel.addEventListener("change", ...) occurrences.
        occurrences = src.count('fillSel.addEventListener("change"')
        assert occurrences >= 2, (
            "fillSel must have at least two change listeners "
            "(syncColorEnabled + refresh-trigger)"
        )

    def test_js_refresh_on_color_input(self):
        # Regression: typing in the manual hex color input must drive a
        # debounced refresh while fill_mode == manual.
        src = self._read()
        idx = src.find('colorInput.addEventListener("input"')
        assert idx != -1, "manual color input must have an input listener"
        block = src[idx:idx + 400]
        assert (
            "refreshAll" in block
        ), "color input must drive a refresh"

    def test_js_caps_client_side_concurrency(self):
        # Without a client cap, ~36 candidate covers fire simultaneous
        # fetches on toggle-on. Each fetch holds a gunicorn worker through
        # the server-side BoundedSemaphore(4) wait, starving login / static
        # routes until the burst drains.
        src = self._read()
        assert (
            "EREADER_MAX_CONCURRENT" in src
        ), "must declare a client-side max-concurrency cap"

    def test_js_uses_generation_for_race_safety(self):
        # The bug was: aborted fetches' .finally decremented a shared
        # inFlightCount that belonged to the NEW burst, corrupting state.
        # The fix is generation-tagged completion handlers — each fetch
        # checks `generation === myGen` before mutating shared UI state.
        src = self._read()
        assert "generation" in src, "must use a generation counter for race safety"
        assert "myGen" in src, "fetches must close over their own generation"
        assert (
            "generation === myGen" in src or "generation == myGen" in src
        ), "completion handlers must gate on generation match"


class TestCoverPreviewConcurrencyCap:
    """Server-side concurrency cap around pad_blob so a single user's burst
    can't starve all gunicorn workers on a multi-user instance.

    Implementation must be a ThreadPoolExecutor (not threading.Semaphore):
    the WSGI server is gevent and a synchronous Semaphore.acquire blocks the
    whole gevent loop. Real OS threads + ImageMagick releasing the GIL gives
    actual parallelism."""

    PAD_FILE = REPO_ROOT / "cps" / "services" / "cover_preview.py"

    def _read(self) -> str:
        return self.PAD_FILE.read_text(encoding="utf-8")

    def test_module_uses_gevent_aware_threadpool(self):
        src = self._read()
        assert "gevent.threadpool" in src, (
            "cover_preview must prefer gevent.threadpool.ThreadPool — "
            "stdlib ThreadPoolExecutor.Future.result() blocks the gevent loop."
        )
        assert "_PREVIEW_POOL_SIZE" in src, "must declare pool size constant"

    def test_module_falls_back_to_stdlib_when_no_gevent(self):
        # The unit-test runner imports the module without gevent installed.
        # The fallback path keeps tests green.
        src = self._read()
        assert "ImportError" in src and "ThreadPoolExecutor" in src, (
            "must fall back to stdlib ThreadPoolExecutor when gevent is missing"
        )

    def test_render_helper_offloads_to_pool(self):
        src = self._read()
        helper_idx = src.find("def render_preview_data_url")
        block = src[helper_idx:helper_idx + 2000]
        assert (
            "_run_in_pool" in block
        ), "render_preview_data_url must dispatch through _run_in_pool"

    def test_run_in_pool_uses_gevent_apply_when_available(self):
        src = self._read()
        helper_idx = src.find("def _run_in_pool")
        assert helper_idx != -1, "must define _run_in_pool helper"
        block = src[helper_idx:helper_idx + 1800]
        assert "_PREVIEW_POOL.apply" in block, (
            "must use ThreadPool.apply (yields to gevent hub) on the gevent path"
        )
        assert ".submit(" in block and ".result()" in block, (
            "must keep stdlib submit/result on the no-gevent fallback path"
        )

    def test_run_in_pool_is_reentrant(self):
        # Nested calls (cover_picker dispatches the whole pipeline; pad_blob
        # internally also dispatches) must not consume two pool slots, or
        # the 4-slot pool deadlocks under load.
        src = self._read()
        helper_idx = src.find("def _run_in_pool")
        block = src[helper_idx:helper_idx + 1800]
        assert "_in_pool_thread" in block, (
            "must track per-thread reentry state to detect nested calls"
        )


# --------------------------------------------------------------------- template surface


class TestCoverPickerTemplateSurface:
    """Static checks that the new picker UI panel is wired into the template."""

    TEMPLATE_FILE = REPO_ROOT / "cps" / "templates" / "cover_picker.html"

    def _read(self) -> str:
        return self.TEMPLATE_FILE.read_text(encoding="utf-8")

    def test_template_gates_panel_on_kobo_padding_enabled(self):
        src = self._read()
        assert (
            "config.config_kobo_cover_padding_enabled" in src
        ), "E-reader preview panel must be conditional on config_kobo_cover_padding_enabled"

    def test_template_includes_ereader_preview_panel_id(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-panel"' in src
        ), "E-reader preview panel needs a stable ID for JS hookup"

    def test_template_includes_ereader_preview_toggle(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-enabled"' in src
        ), "E-reader preview toggle checkbox needs a stable ID"

    def test_template_includes_aspect_select(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-aspect"' in src
        ), "E-reader aspect select needs a stable ID"

    def test_template_includes_fill_mode_select(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-fill-mode"' in src
        ), "E-reader fill-mode select needs a stable ID"

    def test_template_includes_gradient_option(self):
        # The new gradient mode must be selectable in both the picker and
        # the admin Settings dropdowns. (Settings template covered by the
        # admin-side test below.)
        src = self._read()
        assert 'value="gradient"' in src, "picker fill-mode select needs a 'gradient' option"

    def test_template_includes_color_input(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-color"' in src
        ), "E-reader manual-color input needs a stable ID"

    def test_template_passes_ereader_preview_endpoint_to_js(self):
        # Pinned in Task 8 (template rename): the template must call the new
        # endpoint name. If anyone reverts to cover_picker_kobo_preview, the
        # page will raise werkzeug.routing.BuildError at render time -- this
        # assertion catches it before render.
        src = self._read()
        assert (
            "cover_picker.cover_picker_ereader_preview" in src
        ), "the cover-preview endpoint URL must be in window.cwaCoverPicker.endpoints"
        assert (
            "cover_picker.cover_picker_kobo_preview" not in src
        ), "template must not call the legacy endpoint name (BuildError after Task 4)"

    def test_template_includes_loading_status_pill(self):
        src = self._read()
        assert (
            'id="cwa-cover-picker-ereader-status"' in src
        ), "E-reader loading status pill needs a stable ID"


# --------------------------------------------------------------------- legacy URL 308 redirect


class TestLegacyKoboPreviewUrlRedirect:
    """Pin the 308 redirect from /book/<id>/cover/kobo-preview to
    /book/<id>/cover/ereader-preview. The legacy URL must keep working
    for one release after the rename, preserving POST method semantics.
    Remove this test (and the redirect itself) in the release AFTER the
    one shipping the rename.

    These tests inspect the cover_picker.py source directly (rather than
    importing the live blueprint) because cps.cover_picker pulls in
    flask_babel + the full cps package init, which the unit-test layer
    deliberately avoids. The integration suite exercises the live route.
    """

    BLUEPRINT_FILE = REPO_ROOT / "cps" / "cover_picker.py"

    def _read(self) -> str:
        return self.BLUEPRINT_FILE.read_text(encoding="utf-8")

    def test_legacy_route_decorator_is_registered(self):
        """Confirm /book/<int:book_id>/cover/kobo-preview is still wired up
        with a POST method handler — the legacy URL must keep working."""
        src = self._read()
        assert (
            '@cover_picker.route("/book/<int:book_id>/cover/kobo-preview", methods=["POST"])' in src
            or "@cover_picker.route('/book/<int:book_id>/cover/kobo-preview', methods=['POST'])" in src
        ), "legacy /cover/kobo-preview POST route must remain registered"

    def test_legacy_handler_function_name_is_distinct(self):
        """The legacy shim and the canonical handler must be DIFFERENT
        endpoints, not the same handler reachable by two paths. Distinct
        endpoint names let the canonical one be promoted and the legacy
        one retired cleanly in a future release."""
        src = self._read()
        assert "def cover_picker_kobo_preview_legacy(" in src, (
            "legacy shim must be named cover_picker_kobo_preview_legacy"
        )
        assert "def cover_picker_ereader_preview(" in src, (
            "canonical handler must be cover_picker_ereader_preview"
        )
        # Sanity: the legacy name without _legacy suffix must NOT exist as
        # a standalone def (would shadow url_for resolution unpredictably).
        assert "def cover_picker_kobo_preview(" not in src, (
            "old function name cover_picker_kobo_preview must not exist — "
            "use cover_picker_kobo_preview_legacy (shim) or "
            "cover_picker_ereader_preview (canonical)"
        )

    def test_legacy_handler_returns_308_to_canonical(self):
        """The shim must 308-redirect (preserves POST) to the canonical
        URL — not 301 (rewrites to GET on most clients) or 302. Source-
        pin: redirect() called with code=308 and target =
        cover_picker_ereader_preview."""
        src = self._read()
        idx = src.find("def cover_picker_kobo_preview_legacy")
        assert idx != -1, "legacy shim function must exist"
        # Inspect the next ~600 chars — the whole function body.
        body = src[idx:idx + 600]
        assert "redirect(" in body, "legacy shim must call redirect()"
        assert "code=308" in body, (
            "must use HTTP 308 (preserves POST method) — NOT 301 or 302"
        )
        assert "cover_picker.cover_picker_ereader_preview" in body, (
            "must redirect to the canonical cover_picker_ereader_preview endpoint"
        )

    def test_legacy_handler_function_body_is_only_a_redirect(self):
        """Source-pin: the legacy shim must NOT silently grow business
        logic. It's a redirect, period. Future maintainers shouldn't
        adopt it for warts."""
        src = self._read()
        idx = src.find("def cover_picker_kobo_preview_legacy")
        # Find the next top-level def (or end of file)
        next_def = src.find("\ndef ", idx + 1)
        next_route = src.find("\n@cover_picker.route", idx + 1)
        # The body ends at the next def/route decorator, whichever comes first
        candidates = [c for c in (next_def, next_route) if c != -1]
        end = min(candidates) if candidates else len(src)
        body = src[idx:end]
        # Must redirect — that's the entire job.
        assert "redirect(" in body
        assert "code=308" in body
        # No DB writes, no pad_blob, no service calls.
        assert "pad_blob" not in body, "shim must not invoke pad_blob"
        assert "calibre_db" not in body, "shim must not touch the calibre DB"
        assert "ub.session" not in body, "shim must not touch the user DB session"
        assert "render_preview_data_url" not in body, (
            "shim must not invoke the preview helper"
        )
