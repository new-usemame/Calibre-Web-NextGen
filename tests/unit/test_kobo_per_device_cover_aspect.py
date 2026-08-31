# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Cover padding must follow the Kobo that is actually asking.

The padding aspect is a single instance-wide setting. The moment a household
owns two different Kobos, one of them gets covers shaped for the other -- a
Clara BW (1072x1448) served covers padded to a Libra Colour (1264x1680) is a
real, measured configuration on the maintainer's own instance.

Every authenticated Kobo request already carries `x-kobo-devicemodel`, and we
already persist it as `Device.model`. This maps that to the aspect preset we
already ship, and does so through an ALLOW-LIST: an unrecognised model must
fall back to the configured value, never to a guessed ratio.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_cps_stub():
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
            debug=lambda *_a, **_k: None, warning=lambda *_a, **_k: None,
            info=lambda *_a, **_k: None, error=lambda *_a, **_k: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg


@pytest.fixture(scope="module")
def cover_preview():
    _ensure_cps_stub()
    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_preview", REPO_ROOT / "cps" / "services" / "cover_preview.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_preview"] = module
    spec.loader.exec_module(module)
    return module


# Values observed from real hardware on the maintainer's instance, 2026-08-15,
# read out of the `device` table that `x-kobo-devicemodel` populates.
OBSERVED_ON_HARDWARE = [
    ("Kobo Clara BW", "kobo_clara_bw"),
    ("Kobo Libra Colour", "kobo_libra_color"),
]


@pytest.mark.unit
@pytest.mark.parametrize("model,expected", OBSERVED_ON_HARDWARE)
def test_models_seen_on_real_hardware_resolve(cover_preview, model, expected):
    assert cover_preview.preset_for_device_model(model) == expected


@pytest.mark.unit
def test_kobos_british_spelling_is_handled(cover_preview):
    """Kobo ships "Colour", our preset keys say "color". The device wins."""
    assert (cover_preview.preset_for_device_model("Kobo Libra Colour")
            == cover_preview.preset_for_device_model("Kobo Libra Color")
            == "kobo_libra_color")


@pytest.mark.unit
@pytest.mark.parametrize("model,expected", [
    ("Kobo Clara 2E", "kobo_clara_2e"),
    ("Kobo Clara HD", "kobo_clara"),
    ("Kobo Libra 2", "kobo_libra_2"),
    ("Kobo Sage", "kobo_sage"),
    ("Kobo Forma", "kobo_forma"),
    ("Kobo Elipsa 2E", "kobo_elipsa_2e"),
    ("kobo clara bw", "kobo_clara_bw"),
    ("KOBO CLARA BW", "kobo_clara_bw"),
    ("Kobo  Clara   BW", "kobo_clara_bw"),
])
def test_known_families_resolve_case_and_space_insensitively(cover_preview, model, expected):
    assert cover_preview.preset_for_device_model(model) == expected


@pytest.mark.unit
@pytest.mark.parametrize("junk", [
    None, "", "   ", 12345, b"Kobo Clara BW", [],
    "Kobo Nia",                 # real device, no name-matched preset -> must not guess
    "Kobo Aura ONE",            # ditto
    "Not A Kobo",
    "Kobo Clara BW; DROP TABLE device",
    "A" * 200,                  # over the bound device_registry applies
])
def test_anything_unrecognised_falls_back_rather_than_guessing(cover_preview, junk):
    """The header is client-controlled. Returning None keeps the configured
    aspect, which is exactly today's behaviour -- the one safe answer."""
    assert cover_preview.preset_for_device_model(junk) is None


@pytest.mark.unit
def test_every_mapped_preset_actually_exists(cover_preview):
    """A mapping to a preset key we do not ship would resolve to a bogus ratio
    downstream. Catch it here rather than on someone's e-reader."""
    for key in cover_preview._DEVICE_MODEL_PRESETS.values():
        assert key in cover_preview.PRESET_ASPECTS, f"{key} is not a real preset"
        assert key in cover_preview.PRESET_LABELS, f"{key} has no UI label"


@pytest.mark.unit
def test_the_two_household_devices_really_do_disagree(cover_preview):
    """The whole point. If these ratios were equal the feature would be noise."""
    clara = cover_preview.PRESET_ASPECTS["kobo_clara_bw"]
    libra = cover_preview.PRESET_ASPECTS["kobo_libra_color"]
    assert clara != libra
    ratio_clara = clara[0] / clara[1]
    ratio_libra = libra[0] / libra[1]
    # Must exceed the engine's own no-op threshold, or padding would skip anyway.
    assert abs(ratio_clara - ratio_libra) > cover_preview._RATIO_EPSILON


@pytest.mark.unit
def test_aspect_participates_in_the_settings_hash(cover_preview):
    """Two devices must produce two CoverImageIds and two cache entries.
    If the hash ignored the aspect, the first device to fetch a cover would
    poison the cache for the second."""
    def mk(aspect):
        return cover_preview.CoverPreviewSettings(
            enabled=True, target_aspect=aspect, fill_mode="edge_mirror", manual_color="")
    assert mk("kobo_clara_bw").settings_hash() != mk("kobo_libra_color").settings_hash()
