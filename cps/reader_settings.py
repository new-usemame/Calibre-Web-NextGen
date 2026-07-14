# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical validation and defaults for per-user web-reader appearance."""

READER_THEMES = {"lightTheme", "darkTheme", "sepiaTheme", "blackTheme"}
READER_FONTS = {"default", "Yahei", "SimSun", "KaiTi", "Arial"}
READER_SPREADS = {"spread", "nonespread"}

READER_DEFAULTS = {
    "theme": "lightTheme",
    "font": "default",
    "fontSize": 100,
    "margin": 16,
    "lineHeight": 150,
    "spread": "nonespread",
    "reflow": True,
}


def reader_setting_int(value, lo, hi):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(lo, min(hi, int(value)))
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return max(lo, min(hi, int(value.strip())))
    return None


def sanitize_reader_settings(payload):
    """Return only known, typed reader settings from an arbitrary mapping."""
    if not isinstance(payload, dict):
        return {}
    out = {}
    if payload.get("theme") in READER_THEMES:
        out["theme"] = payload["theme"]
    if payload.get("font") in READER_FONTS:
        out["font"] = payload["font"]
    if payload.get("spread") in READER_SPREADS:
        out["spread"] = payload["spread"]
    for key, lo, hi in (
        ("fontSize", 75, 200),
        ("margin", 0, 80),
        ("lineHeight", 100, 220),
    ):
        value = reader_setting_int(payload.get(key), lo, hi)
        if value is not None:
            out[key] = value
    reflow = payload.get("reflow")
    if isinstance(reflow, bool):
        out["reflow"] = reflow
    elif isinstance(reflow, str) and reflow.strip().lower() in {"true", "false"}:
        out["reflow"] = reflow.strip().lower() == "true"
    return out


def merged_reader_settings(current, patch):
    """Merge a partial client patch without erasing unrelated saved controls."""
    merged = sanitize_reader_settings(current)
    merged.update(sanitize_reader_settings(patch))
    return merged


def resolved_reader_settings(current):
    """Return the complete client contract, applying defaults to missing keys."""
    resolved = dict(READER_DEFAULTS)
    resolved.update(sanitize_reader_settings(current))
    return resolved
