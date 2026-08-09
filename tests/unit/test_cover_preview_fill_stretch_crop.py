# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for the two frame-filling cover modes (issue #1280).

The six original fill modes all *pad* the cover out to the device aspect,
keeping the artwork at its original proportions and adding a border. #1280
asked for modes that fill the frame with the artwork itself:

  * ``stretch``     — non-uniform scale. Nothing is cropped, the artwork is
                      slightly squashed or elongated on one axis.
  * ``scale_crop``  — centered crop to the target ratio. Nothing is
                      distorted, a strip is lost off the two long edges.

The geometry is a pure function (``_compute_crop_box``) so the maths is
covered on any dev box. The pixel-level tests are gated on Wand/ImageMagick
the same way the sibling engine suite is.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_preview_module():
    """Idempotently top up the cps stub so this test co-exists with
    sibling service tests."""
    module_path = REPO_ROOT / "cps" / "services" / "cover_preview.py"

    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

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

    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_preview", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_preview"] = module
    spec.loader.exec_module(module)
    return module


preview = _load_preview_module()

# Kobo Libra Color: 1264x1680 -> 0.7523...
LIBRA_RATIO = 1264 / 1680


# ---------------------------------------------------------------------------
# Mode registration
# ---------------------------------------------------------------------------

class TestModesRegistered:
    def test_stretch_is_a_public_fill_mode(self):
        assert "stretch" in preview.FILL_MODES

    def test_scale_crop_is_a_public_fill_mode(self):
        assert "scale_crop" in preview.FILL_MODES

    def test_the_six_original_modes_are_untouched(self):
        for mode in ("edge_mirror", "edge_blur", "gradient",
                     "average", "dominant", "manual"):
            assert mode in preview.FILL_MODES

    def test_default_is_still_edge_mirror(self):
        """Adding frame-filling modes must not change what existing users get."""
        assert preview.DEFAULT_FILL_MODE == "edge_mirror"


# ---------------------------------------------------------------------------
# Crop geometry — pure, runs without ImageMagick
# ---------------------------------------------------------------------------

class TestComputeCropBox:
    def test_too_tall_source_trims_top_and_bottom(self):
        """A 2:3 cover into a 1264:1680 frame is too tall: width is kept and
        height comes off the top and bottom."""
        left, top, w, h = preview._compute_crop_box(1000, 1500, LIBRA_RATIO)
        assert (left, w) == (0, 1000), "width must be preserved"
        assert h < 1500, "height must be trimmed"
        assert top > 0
        assert abs((w / h) - LIBRA_RATIO) < 0.01

    def test_too_wide_source_trims_left_and_right(self):
        left, top, w, h = preview._compute_crop_box(1600, 1000, LIBRA_RATIO)
        assert (top, h) == (0, 1000), "height must be preserved"
        assert w < 1600, "width must be trimmed"
        assert left > 0
        assert abs((w / h) - LIBRA_RATIO) < 0.01

    def test_crop_is_centered(self):
        """The trim is split evenly, so the middle of the artwork survives."""
        left, top, w, h = preview._compute_crop_box(1000, 1500, LIBRA_RATIO)
        trimmed_above = top
        trimmed_below = 1500 - (top + h)
        assert abs(trimmed_above - trimmed_below) <= 1

    def test_already_on_ratio_is_a_noop(self):
        left, top, w, h = preview._compute_crop_box(1264, 1680, LIBRA_RATIO)
        assert (left, top, w, h) == (0, 0, 1264, 1680)

    def test_crop_never_exceeds_the_source(self):
        for src_w, src_h in ((10, 10000), (10000, 10), (3, 4), (1, 1)):
            left, top, w, h = preview._compute_crop_box(src_w, src_h, LIBRA_RATIO)
            assert 1 <= w <= src_w
            assert 1 <= h <= src_h
            assert left >= 0 and top >= 0
            assert left + w <= src_w
            assert top + h <= src_h

    @pytest.mark.parametrize("src_w,src_h", [(0, 100), (100, 0), (-5, 100), (0, 0)])
    def test_degenerate_dims_do_not_raise(self, src_w, src_h):
        assert preview._compute_crop_box(src_w, src_h, LIBRA_RATIO) == (0, 0, src_w, src_h)

    def test_degenerate_ratio_does_not_raise(self):
        assert preview._compute_crop_box(100, 200, 0) == (0, 0, 100, 200)

    def test_crop_loses_less_than_padding_adds(self):
        """Sanity check that crop and pad are inverse strategies on the same
        source: padding grows the short axis, cropping shrinks the long one."""
        pad_w, pad_h, orient = preview._compute_padded_dims(1000, 1500, LIBRA_RATIO)
        _, _, crop_w, crop_h = preview._compute_crop_box(1000, 1500, LIBRA_RATIO)
        assert orient == "horizontal"
        assert pad_w > 1000 and pad_h == 1500      # pad widens
        assert crop_w == 1000 and crop_h < 1500    # crop shortens


# ---------------------------------------------------------------------------
# Single source of truth across backend / SPA / Jinja
#
# The mode list is duplicated in four places and they must agree byte-for-byte
# or a user picks an option the backend then rejects with a 400. This pins the
# correspondence (community-PR anti-slop bar, rule 3).
# ---------------------------------------------------------------------------

def _frontend_fill_modes() -> list[str]:
    src = (REPO_ROOT / "frontend" / "src" / "lib" / "coverPicker.ts").read_text()
    block = re.search(
        r"EREADER_FILL_MODES[^=]*=\s*\[(.*?)\];", src, re.S
    )
    assert block, "EREADER_FILL_MODES not found in coverPicker.ts"
    return re.findall(r"value:\s*'([^']+)'", block.group(1))


def _template_fill_modes(rel_path: str, select_id: str) -> list[str]:
    src = (REPO_ROOT / rel_path).read_text()
    block = re.search(
        r'<select[^>]*id="%s".*?</select>' % re.escape(select_id), src, re.S
    )
    assert block, f"fill-mode select {select_id} not found in {rel_path}"
    return re.findall(r'<option value="([^"]+)"', block.group(0))


class TestFillModeListsAgree:
    def test_spa_list_matches_backend(self):
        assert sorted(_frontend_fill_modes()) == sorted(preview.FILL_MODES)

    def test_admin_config_template_matches_backend(self):
        modes = _template_fill_modes(
            "cps/templates/config_edit.html", "config_kobo_cover_padding_fill_mode"
        )
        assert sorted(modes) == sorted(preview.FILL_MODES)

    def test_cover_picker_template_matches_backend(self):
        modes = _template_fill_modes(
            "cps/templates/cover_picker.html", "cwa-cover-picker-ereader-fill-mode"
        )
        assert sorted(modes) == sorted(preview.FILL_MODES)

    def test_spa_labels_are_anchored_for_translation(self):
        """SPA-only msgids must be anchored in cps/spa_strings.py or the
        extraction pass strips them."""
        src = (REPO_ROOT / "frontend" / "src" / "lib" / "coverPicker.ts").read_text()
        block = re.search(r"EREADER_FILL_MODES[^=]*=\s*\[(.*?)\];", src, re.S)
        labels = re.findall(r"label:\s*'([^']+)'", block.group(1))
        anchors = (REPO_ROOT / "cps" / "spa_strings.py").read_text()
        missing = [lbl for lbl in labels if lbl not in anchors]
        assert not missing, f"unanchored SPA msgids: {missing}"


# ---------------------------------------------------------------------------
# Pixel behaviour — needs ImageMagick
# ---------------------------------------------------------------------------

requires_im = pytest.mark.skipif(
    not preview.use_IM, reason="ImageMagick/Wand not installed"
)


def _source_jpeg(width: int, height: int) -> bytes:
    from wand.color import Color
    from wand.image import Image
    with Image(width=width, height=height, background=Color("#3060a0")) as img:
        img.format = "jpeg"
        return img.make_blob()


def _dims(blob: bytes):
    from wand.image import Image
    with Image(blob=blob) as img:
        return img.width, img.height


def _settings(mode: str):
    return preview.CoverPreviewSettings(
        enabled=True, target_aspect="kobo_libra_color", fill_mode=mode, manual_color=""
    )


@requires_im
class TestRenderedOutput:
    def test_stretch_fills_the_frame_without_cropping(self):
        out = preview.pad_blob(_source_jpeg(1000, 1500), _settings("stretch"))
        w, h = _dims(out)
        assert abs((w / h) - LIBRA_RATIO) < 0.01
        assert w > 1000, "stretch scales the short axis up to the frame"
        assert h == 1500, "stretch must not crop the long axis"

    def test_scale_crop_fills_the_frame_by_trimming(self):
        out = preview.pad_blob(_source_jpeg(1000, 1500), _settings("scale_crop"))
        w, h = _dims(out)
        assert abs((w / h) - LIBRA_RATIO) < 0.01
        assert w == 1000, "crop preserves the short axis"
        assert h < 1500, "crop trims the long axis"

    def test_new_modes_differ_from_padding(self):
        src = _source_jpeg(1000, 1500)
        mirror = preview.pad_blob(src, _settings("edge_mirror"))
        stretch = preview.pad_blob(src, _settings("stretch"))
        crop = preview.pad_blob(src, _settings("scale_crop"))
        assert _dims(stretch) != _dims(crop)
        assert stretch != mirror and crop != mirror

    def test_already_on_ratio_returns_source_untouched(self):
        src = _source_jpeg(1264, 1680)
        for mode in ("stretch", "scale_crop"):
            assert preview.pad_blob(src, _settings(mode)) == src

    def test_wide_source_is_handled_on_the_other_axis(self):
        out = preview.pad_blob(_source_jpeg(1600, 1000), _settings("scale_crop"))
        w, h = _dims(out)
        assert h == 1000 and w < 1600
        assert abs((w / h) - LIBRA_RATIO) < 0.01

    def test_settings_hash_separates_the_new_modes(self):
        """Cache keys must not collide, or switching mode serves a stale image."""
        hashes = {m: _settings(m).settings_hash() for m in preview.FILL_MODES}
        assert len(set(hashes.values())) == len(preview.FILL_MODES)
