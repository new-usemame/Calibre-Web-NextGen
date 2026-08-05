# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Aspect-ratio padding for book covers served at e-reader dimensions.

This module pads source covers to a target aspect ratio server-side so
the consuming surface (Kobo sync, library preview, shelf rendering)
receives an image that already matches its target screen and renders
without letterboxing or pillarboxing.

Supported targets include common Kobo / Kindle / PocketBook / Boox
models plus a custom W×H fallback. Fill modes are `edge_mirror` (extend
the artwork), `edge_blur` (blurred extension), `gradient`, `average` or
`dominant` color, and `manual` solid color.

This was originally the Kobo-sync padding pipeline. The engine is
device-neutral; "Kobo" in the codebase now refers only to the Kobo sync
protocol layer above this module, not to this rendering itself.
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Tuple

from .. import logger
from . import parallel

# gevent-aware thread pool: gevent.threadpool.ThreadPool yields the gevent
# loop while a worker thread is busy, so other greenlets keep running. The
# stdlib ThreadPoolExecutor's Future.result() does a SYNCHRONOUS wait
# that blocks the entire gevent loop — confirmed live with py-spy: MainThread
# stuck in concurrent.futures._base.wait while a worker did Wand work,
# and login / static requests piled up unanswered. We fall back to the stdlib
# executor in environments without gevent (notably the unit-test runner)
# so the module imports cleanly outside the production WSGI server.
try:  # pragma: no cover - environment branch
    from gevent.threadpool import ThreadPool as _GeventThreadPool  # type: ignore
    _HAVE_GEVENT_POOL = True
except ImportError:  # pragma: no cover - environment branch
    _GeventThreadPool = None  # type: ignore
    _HAVE_GEVENT_POOL = False

log = logger.create()

try:
    from wand.color import Color
    from wand.image import Image
    use_IM = True
except (ImportError, RuntimeError):  # ImageMagick missing → degrade gracefully
    use_IM = False
    Color = None  # type: ignore
    Image = None  # type: ignore


# Public fill-mode identifiers. Anything else falls through to "edge_mirror".
FILL_MODES = ("edge_mirror", "edge_blur", "gradient", "average", "dominant", "manual")
DEFAULT_FILL_MODE = "edge_mirror"

# Aspect-ratio presets grouped by manufacturer. Values are device screen
# resolutions in PIXELS — width × height when the device is held in
# portrait. The padding engine only uses the ratio (W/H), not absolute
# dimensions, so a "1264×1680" entry just locks the ratio to 1264:1680
# regardless of actual cover resolution.
#
# Sources: device-spec pages from each manufacturer. Verified 2026-05.
PRESET_ASPECTS = {
    # Kobo
    "kobo_libra_color":   (1264, 1680),
    "kobo_libra_2":       (1264, 1680),
    "kobo_clara_color":   (1072, 1448),
    "kobo_clara_bw":      (1072, 1448),
    "kobo_clara_2e":      (1072, 1448),
    "kobo_clara":         (1072, 1448),
    "kobo_sage":          (1440, 1920),
    "kobo_elipsa_2e":     (1404, 1872),
    "kobo_forma":         (1440, 1920),
    # Kindle
    "kindle_paperwhite":  (1236, 1648),
    "kindle_oasis":       (1264, 1680),
    "kindle_basic":       (1072, 1448),
    "kindle_scribe":      (1860, 2480),
    # PocketBook
    "pocketbook_era":     (1264, 1680),
    "pocketbook_inkpad":  (1404, 1872),
    "pocketbook_color":   (1264, 1680),
    # Boox
    "boox_page":          (1264, 1680),
    "boox_leaf":          (1264, 1680),
    "boox_note_air":      (1404, 1872),
    # Generic e-ink classes
    "generic_6in_eink":   (1072, 1448),
    "generic_7in_eink":   (1264, 1680),
    "generic_10in_eink":  (1404, 1872),
}

# Human-friendly labels grouped for the UI. Order intentionally
# matches PRESET_GROUPS below — the dict insertion order is iteration
# order in Python 3.7+, so dropdowns can iterate this dict directly.
PRESET_LABELS = {
    "kobo_libra_color":   "Kobo Libra Color (1264×1680)",
    "kobo_libra_2":       "Kobo Libra 2 (1264×1680)",
    "kobo_clara_color":   "Kobo Clara Color (1072×1448)",
    "kobo_clara_bw":      "Kobo Clara BW (1072×1448)",
    "kobo_clara_2e":      "Kobo Clara 2E (1072×1448)",
    "kobo_clara":         "Kobo Clara HD (1072×1448)",
    "kobo_sage":          "Kobo Sage (1440×1920)",
    "kobo_elipsa_2e":     "Kobo Elipsa 2E (1404×1872)",
    "kobo_forma":         "Kobo Forma (1440×1920)",
    "kindle_paperwhite":  "Kindle Paperwhite (1236×1648)",
    "kindle_oasis":       "Kindle Oasis (1264×1680)",
    "kindle_basic":       "Kindle Basic (1072×1448)",
    "kindle_scribe":      "Kindle Scribe (1860×2480)",
    "pocketbook_era":     "PocketBook Era (1264×1680)",
    "pocketbook_inkpad":  "PocketBook InkPad (1404×1872)",
    "pocketbook_color":   "PocketBook Color (1264×1680)",
    "boox_page":          "Boox Page (1264×1680)",
    "boox_leaf":          "Boox Leaf (1264×1680)",
    "boox_note_air":      "Boox Note Air (1404×1872)",
    "generic_6in_eink":   "Generic 6\" e-ink (1072×1448)",
    "generic_7in_eink":   "Generic 7\" e-ink (1264×1680)",
    "generic_10in_eink":  "Generic 10\" e-ink (1404×1872)",
}

# Manufacturer-grouped tuples for rendering optgroup-style dropdowns.
# Iterate these in the UI; each tuple is (group_label, (key, key, ...)).
PRESET_GROUPS = (
    ("Kobo", ("kobo_libra_color", "kobo_libra_2", "kobo_clara_color",
              "kobo_clara_bw", "kobo_clara_2e", "kobo_clara",
              "kobo_sage", "kobo_elipsa_2e", "kobo_forma")),
    ("Kindle", ("kindle_paperwhite", "kindle_oasis", "kindle_basic", "kindle_scribe")),
    ("PocketBook", ("pocketbook_era", "pocketbook_inkpad", "pocketbook_color")),
    ("Boox", ("boox_page", "boox_leaf", "boox_note_air")),
    ("Generic e-ink", ("generic_6in_eink", "generic_7in_eink", "generic_10in_eink")),
)

DEFAULT_PRESET = "kobo_libra_color"

# How close the source must be to the target ratio to skip padding entirely.
_RATIO_EPSILON = 0.005

# Edge-strip width for blur/mirror sampling (pixels). Larger = more of the
# cover's edge bleeds into the pad area; smaller = pad area looks more
# uniform. 24 px is a good middle for ~1500px-tall covers.
_EDGE_STRIP_PX = 24

# JPEG output quality. Matches the thumbnail task's pin.
_JPEG_QUALITY = 88


@dataclass(frozen=True)
class CoverPreviewSettings:
    """Snapshot of the admin's Kobo-padding configuration.

    Hashable + frozen so it can be passed around safely and used as a cache key.
    """
    enabled: bool
    target_aspect: str  # "WxH" or a preset key, e.g. "1264x1680" or "kobo_libra_color"
    fill_mode: str
    manual_color: str  # hex with leading # when fill_mode == "manual"; "" otherwise

    def settings_hash(self) -> str:
        """Stable short hash of the settings tuple. Used in cache filenames
        and (optionally) appended to CoverImageId for device-side cache
        invalidation when settings change.

        Only hashes fields that actually affect rendered output:
        manual_color is ignored unless fill_mode is "manual", so toggling
        it while in another mode doesn't bust the cache.
        """
        if not self.enabled:
            return "off"
        effective_color = self.manual_color or "" if self.fill_mode == "manual" else ""
        parts = [self.target_aspect, self.fill_mode, effective_color]
        digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:10]


def parse_target_ratio(target_aspect: str) -> float:
    """Resolve a preset name or 'WxH' string to a float ratio (W/H).

    Falls back to the Libra Color default if the input is malformed.
    """
    if not target_aspect:
        w, h = PRESET_ASPECTS[DEFAULT_PRESET]
        return w / h

    if target_aspect in PRESET_ASPECTS:
        w, h = PRESET_ASPECTS[target_aspect]
        return w / h

    # WxH form, e.g. "1264x1680" or "1264:1680"
    sep = "x" if "x" in target_aspect else (":" if ":" in target_aspect else None)
    if sep:
        try:
            w_str, h_str = target_aspect.split(sep, 1)
            w, h = int(w_str.strip()), int(h_str.strip())
            if w > 0 and h > 0:
                return w / h
        except (ValueError, TypeError):
            pass

    log.warning("cover_preview: unrecognized target_aspect %r, using default", target_aspect)
    w, h = PRESET_ASPECTS[DEFAULT_PRESET]
    return w / h


def _hex_to_color(hex_str: str):
    """Wand Color object from '#RRGGBB' (or fallback to white on bad input)."""
    if not hex_str:
        return Color("white")
    candidate = hex_str.strip()
    if not candidate.startswith("#"):
        candidate = "#" + candidate
    try:
        return Color(candidate)
    except Exception:  # Wand raises ValueError-likes for unparseable colors
        log.warning("cover_preview: invalid manual color %r, using white", hex_str)
        return Color("white")


def _color_to_hex(c) -> str:
    """Wand Color → '#rrggbb'. Wand exposes 0-1 floats per channel."""
    r = max(0, min(255, int(round(c.red * 255))))
    g = max(0, min(255, int(round(c.green * 255))))
    b = max(0, min(255, int(round(c.blue * 255))))
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def average_color_hex(img) -> str:
    """Mean color across the image. Cheap: resize-to-1x1 lets ImageMagick do
    the averaging in C."""
    with img.clone() as scaled:
        scaled.resize(1, 1)
        return _color_to_hex(scaled[0, 0])


def dominant_color_hex(img) -> str:
    """Quantized-mode color. Resize to 64x64, quantize to 8 colors, pick the
    bucket with the most pixels. Avoids histogram-of-full-image cost while
    still capturing the visual mode."""
    with img.clone() as small:
        small.resize(64, 64)
        try:
            small.quantize(8, dither=False)
        except Exception:
            # Older Wand versions may not accept all quantize kwargs; the
            # average still works as a fallback.
            return average_color_hex(img)
        hist = small.histogram
        if not hist:
            return average_color_hex(img)
        winner = max(hist.items(), key=lambda kv: kv[1])[0]
        return _color_to_hex(winner)


def _compute_padded_dims(src_w: int, src_h: int, target_ratio: float) -> Tuple[int, int, str]:
    """Return (new_w, new_h, orient) where orient is 'horizontal' (left+right
    pads) or 'vertical' (top+bottom pads). 'noop' if already on target."""
    if src_w <= 0 or src_h <= 0:
        return src_w, src_h, "noop"
    src_ratio = src_w / src_h
    if abs(src_ratio - target_ratio) < _RATIO_EPSILON:
        return src_w, src_h, "noop"
    if src_ratio > target_ratio:
        # too wide → pad top + bottom
        return src_w, int(round(src_w / target_ratio)), "vertical"
    # too tall → pad left + right
    return int(round(src_h * target_ratio)), src_h, "horizontal"


def _composite_solid(img, new_w: int, new_h: int, orient: str, fill_color):
    """Solid-color pad. Returns a new Image owned by the caller (must close)."""
    canvas = Image(width=new_w, height=new_h, background=fill_color)
    if orient == "vertical":
        offset_x, offset_y = 0, (new_h - img.height) // 2
    else:
        offset_x, offset_y = (new_w - img.width) // 2, 0
    canvas.composite(img, left=offset_x, top=offset_y)
    return canvas


def _tile_strip(canvas, strip, axis_origin: int, axis_size: int, orient: str):
    """Tile a strip across `axis_size` px, starting at axis_origin. The strip
    is composited side-by-side (orient='horizontal') or stacked
    (orient='vertical') for cases where the pad area is wider/taller than
    the source strip's dimension."""
    if orient == "horizontal":
        # tile horizontally: strip.width steps along x
        x = axis_origin
        end = axis_origin + axis_size
        while x < end:
            canvas.composite(strip, left=x, top=0)
            x += max(1, strip.width)
    else:
        y = axis_origin
        end = axis_origin + axis_size
        while y < end:
            canvas.composite(strip, left=0, top=y)
            y += max(1, strip.height)


def _composite_edge_mirror(img, new_w: int, new_h: int, orient: str):
    """Mirror the source's outer edges into the pad area. Looks like the
    artwork extends naturally into the border."""
    canvas = Image(width=new_w, height=new_h, background=Color("white"))
    if orient == "horizontal":
        pad_total = new_w - img.width
        left_pad = pad_total // 2
        right_pad = pad_total - left_pad
        # Left pad: leftmost min(left_pad, img.width) → flop → place
        if left_pad > 0:
            strip_w = min(left_pad, img.width)
            with img.clone() as strip:
                strip.crop(left=0, top=0, width=strip_w, height=img.height)
                strip.flop()
                if strip.width >= left_pad:
                    canvas.composite(strip, left=0, top=0)
                else:
                    _tile_strip(canvas, strip, 0, left_pad, "horizontal")
        if right_pad > 0:
            strip_w = min(right_pad, img.width)
            with img.clone() as strip:
                strip.crop(left=img.width - strip_w, top=0, width=strip_w, height=img.height)
                strip.flop()
                start = left_pad + img.width
                if strip.width >= right_pad:
                    canvas.composite(strip, left=start, top=0)
                else:
                    _tile_strip(canvas, strip, start, right_pad, "horizontal")
        canvas.composite(img, left=left_pad, top=0)
    else:  # vertical
        pad_total = new_h - img.height
        top_pad = pad_total // 2
        bottom_pad = pad_total - top_pad
        if top_pad > 0:
            strip_h = min(top_pad, img.height)
            with img.clone() as strip:
                strip.crop(left=0, top=0, width=img.width, height=strip_h)
                strip.flip()
                if strip.height >= top_pad:
                    canvas.composite(strip, left=0, top=0)
                else:
                    _tile_strip(canvas, strip, 0, top_pad, "vertical")
        if bottom_pad > 0:
            strip_h = min(bottom_pad, img.height)
            with img.clone() as strip:
                strip.crop(left=0, top=img.height - strip_h, width=img.width, height=strip_h)
                strip.flip()
                start = top_pad + img.height
                if strip.height >= bottom_pad:
                    canvas.composite(strip, left=0, top=start)
                else:
                    _tile_strip(canvas, strip, start, bottom_pad, "vertical")
        canvas.composite(img, left=0, top=top_pad)
    return canvas


def _band_dominant_hex(img, top: int, height: int) -> str:
    """Dominant color of a horizontal band (top..top+height) of `img`.
    Used by `gradient` fill mode to sample a palette-matched anchor color
    without averaging the whole cover. Falls back to the cover's average
    if anything goes wrong with the crop."""
    band_h = max(1, min(height, img.height - top))
    try:
        with img.clone() as band:
            band.crop(left=0, top=top, width=img.width, height=band_h)
            return dominant_color_hex(band)
    except Exception:
        return average_color_hex(img)


def _composite_gradient(img, new_w: int, new_h: int, orient: str):
    """Two-color gradient pad. Sample dominant color from the cover's top
    edge and bottom edge, then fill the pad area with a smooth top-to-bottom
    gradient between them. Looks 'designed' — colors come from the artwork
    palette, but the pad area itself is clean (no edge-replication artifacts).

    Best for covers with a strong top/bottom color split (sky-over-ground,
    title-band-over-art); falls back gracefully on monochromatic covers
    where the two band colors are nearly identical.

    Wand's ``pseudo='gradient:'`` constructor renders the gradient in C —
    cheap relative to the per-pixel composites done by mirror/blur.
    """
    canvas = Image(width=new_w, height=new_h, background=Color("white"))

    # ~10% of cover height for each sampling band — large enough that the
    # dominant-color quantization has signal, small enough that it picks up
    # the actual edge palette rather than the cover's overall mean.
    band_h = max(2, img.height // 10)
    top_hex = _band_dominant_hex(img, 0, band_h)
    bot_hex = _band_dominant_hex(img, max(0, img.height - band_h), band_h)
    pseudo = "gradient:{}-{}".format(top_hex, bot_hex)

    if orient == "horizontal":
        pad_total = new_w - img.width
        left_pad = pad_total // 2
        right_pad = pad_total - left_pad
        if left_pad > 0:
            try:
                bar = Image(width=left_pad, height=new_h, pseudo=pseudo)
            except Exception:
                # Some IM builds reject very narrow gradient widths; fall
                # back to a solid bar of the top color rather than failing.
                bar = Image(width=left_pad, height=new_h, background=_hex_to_color(top_hex))
            try:
                canvas.composite(bar, left=0, top=0)
            finally:
                bar.close()
        if right_pad > 0:
            try:
                bar = Image(width=right_pad, height=new_h, pseudo=pseudo)
            except Exception:
                bar = Image(width=right_pad, height=new_h, background=_hex_to_color(top_hex))
            try:
                canvas.composite(bar, left=left_pad + img.width, top=0)
            finally:
                bar.close()
        canvas.composite(img, left=left_pad, top=0)
    else:  # vertical pads — top + bottom
        pad_total = new_h - img.height
        top_pad = pad_total // 2
        bottom_pad = pad_total - top_pad
        # Top pad: solid top-edge dominant. Bottom pad: solid bottom-edge
        # dominant. A vertical gradient inside each pad is overkill here
        # because each pad is short relative to the cover; solid bands tied
        # to the cover's edge color hide the seam more cleanly.
        if top_pad > 0:
            with Image(width=new_w, height=top_pad, background=_hex_to_color(top_hex)) as bar:
                canvas.composite(bar, left=0, top=0)
        if bottom_pad > 0:
            with Image(width=new_w, height=bottom_pad, background=_hex_to_color(bot_hex)) as bar:
                canvas.composite(bar, left=0, top=top_pad + img.height)
        canvas.composite(img, left=0, top=top_pad)
    return canvas


def _blurred_strip(strip, target_w: int, target_h: int):
    """Stretch ``strip`` to (target_w x target_h) and apply a heavy blur, but
    do the blur at 1/4 resolution and upscale. Bilinear upscale is itself
    a low-pass filter, so the eye can't tell — and the work drops from
    O(target_w * target_h * sigma) to roughly 1/16 of that. Saves ~70% of
    edge_blur's wall-clock without changing the visual output noticeably.

    The caller passes a freshly-cloned strip; this function consumes it
    (mutates + returns) so callers should not reuse the input.
    """
    work_w = max(8, target_w // 4)
    work_h = max(8, target_h // 4)
    strip.resize(work_w, work_h)
    # sigma scales with the working resolution. Original was sigma=20 on
    # full-size; quartering both dims => sigma=5 on the down-sampled image
    # gives the same effective blur radius after upscale.
    strip.blur(radius=0, sigma=5)
    strip.resize(target_w, target_h)
    return strip


def _composite_edge_blur(img, new_w: int, new_h: int, orient: str):
    """Stretch the outer edge across the pad area and blur heavily. A softer,
    bokeh-like alternative to edge_mirror that hides edge artifacts.

    Optimized: blur at 1/4 resolution and upscale (~3x faster than the
    naive full-resolution blur with sigma=20). The bilinear upscale acts
    as a low-pass filter so the visual difference is imperceptible.
    """
    canvas = Image(width=new_w, height=new_h, background=Color("white"))
    if orient == "horizontal":
        pad_total = new_w - img.width
        left_pad = pad_total // 2
        right_pad = pad_total - left_pad
        sample_w = max(2, min(_EDGE_STRIP_PX, img.width))
        if left_pad > 0:
            with img.clone() as strip:
                strip.crop(left=0, top=0, width=sample_w, height=img.height)
                _blurred_strip(strip, left_pad, img.height)
                canvas.composite(strip, left=0, top=0)
        if right_pad > 0:
            with img.clone() as strip:
                strip.crop(left=img.width - sample_w, top=0, width=sample_w, height=img.height)
                _blurred_strip(strip, right_pad, img.height)
                canvas.composite(strip, left=left_pad + img.width, top=0)
        canvas.composite(img, left=left_pad, top=0)
    else:
        pad_total = new_h - img.height
        top_pad = pad_total // 2
        bottom_pad = pad_total - top_pad
        sample_h = max(2, min(_EDGE_STRIP_PX, img.height))
        if top_pad > 0:
            with img.clone() as strip:
                strip.crop(left=0, top=0, width=img.width, height=sample_h)
                _blurred_strip(strip, img.width, top_pad)
                canvas.composite(strip, left=0, top=0)
        if bottom_pad > 0:
            with img.clone() as strip:
                strip.crop(left=0, top=img.height - sample_h, width=img.width, height=sample_h)
                _blurred_strip(strip, img.width, bottom_pad)
                canvas.composite(strip, left=0, top=top_pad + img.height)
        canvas.composite(img, left=0, top=top_pad)
    return canvas


def pad_blob(blob: bytes, settings: CoverPreviewSettings) -> bytes:
    """Top-level pure entry point. JPEG bytes in, JPEG bytes out.

    No-ops (returns input unchanged) when:
      - Wand isn't installed
      - settings.enabled is False
      - the source is already at the target ratio (within epsilon)
      - the blob isn't decodable as an image
    """
    if not use_IM or not settings.enabled or not blob:
        return blob

    target_ratio = parse_target_ratio(settings.target_aspect)
    fill_mode = settings.fill_mode if settings.fill_mode in FILL_MODES else DEFAULT_FILL_MODE

    try:
        with Image(blob=blob) as img:
            new_w, new_h, orient = _compute_padded_dims(img.width, img.height, target_ratio)
            if orient == "noop":
                return blob

            # Force RGB so JPEG output is well-formed even if the source was
            # palette-indexed or grayscale.
            try:
                img.transform_colorspace("srgb")
            except Exception:
                pass

            if fill_mode == "manual":
                fill_color = _hex_to_color(settings.manual_color)
                padded = _composite_solid(img, new_w, new_h, orient, fill_color)
            elif fill_mode == "average":
                fill_color = _hex_to_color(average_color_hex(img))
                padded = _composite_solid(img, new_w, new_h, orient, fill_color)
            elif fill_mode == "dominant":
                fill_color = _hex_to_color(dominant_color_hex(img))
                padded = _composite_solid(img, new_w, new_h, orient, fill_color)
            elif fill_mode == "edge_blur":
                padded = _composite_edge_blur(img, new_w, new_h, orient)
            elif fill_mode == "gradient":
                padded = _composite_gradient(img, new_w, new_h, orient)
            else:  # edge_mirror (default)
                padded = _composite_edge_mirror(img, new_w, new_h, orient)

            try:
                padded.format = "jpeg"
                padded.compression_quality = _JPEG_QUALITY
                out = padded.make_blob()
                return out
            finally:
                padded.close()
    except Exception as ex:
        log.warning("cover_preview: pad_blob failed (%s); returning source", ex)
        return blob


# Bound concurrent Wand work. Wand is CPU-heavy (50-500 ms per call) and
# the cover-picker can fire 60+ pad requests at once when a user toggles
# Kobo preview on. Two requirements:
#
# 1. Real OS-thread parallelism — the WSGI server is gevent (single OS
#    thread, cooperative scheduling) and we don't call gevent.monkey.patch_all().
#    A threading.Semaphore.acquire() would block the *whole gevent loop*.
#    ImageMagick releases the GIL during its C extensions, so 4 worker
#    threads really process four pad-jobs at once.
#
# 2. The greenlet that submits a job must yield while waiting — otherwise
#    the gevent loop is blocked for the duration of the Wand call and
#    login / static / metadata-search requests pile up unanswered.
#    concurrent.futures.Future.result() does a synchronous threading.Event
#    wait that does NOT yield to gevent (confirmed live with py-spy:
#    MainThread stuck in concurrent.futures._base.wait while a worker did
#    Wand work). gevent.threadpool.ThreadPool is the gevent-aware analogue;
#    its apply()/spawn().get() yield through the hub.
#
# We prefer the gevent pool in production (when gevent is importable) and
# fall back to the stdlib executor for test runners that don't import
# gevent. Both honour the 4-worker cap.
# Pool size 8: most workers spend their time blocked on external SSL reads
# (Amazon, OpenLibrary, Google Books) when fetching candidate covers. Wand
# work itself is fast (~0.2 s/cover) but the fetch can hit the 8-second
# read timeout. 8 threads lets 8 covers flow through fetch+pad concurrently;
# the GIL doesn't block parallelism because urllib3's socket reads release
# it, and so does ImageMagick's C code. 8 is well below any reasonable
# worker count and won't starve other endpoints — the gevent loop stays
# responsive throughout because each greenlet yields via gevent.threadpool.
_PREVIEW_POOL_SIZE = 8
if _HAVE_GEVENT_POOL:
    _PREVIEW_POOL = _GeventThreadPool(_PREVIEW_POOL_SIZE)
else:
    _PREVIEW_POOL = ThreadPoolExecutor(max_workers=_PREVIEW_POOL_SIZE, thread_name_prefix="kobo-preview")
# Backward-compat name kept so existing tests/audits referencing
# `_PREVIEW_EXECUTOR` still resolve. Both names point at the same object.
_PREVIEW_EXECUTOR = _PREVIEW_POOL


_in_pool_thread = threading.local()


def _run_in_pool(fn, *args, **kwargs):
    """Submit ``fn(*args, **kwargs)`` to the preview pool and wait for it.
    Yields cleanly to other greenlets while the worker thread is busy.

    Reentrant: if we're already executing on a worker thread of this pool
    (e.g. ``cover_picker_kobo_preview`` dispatched a fetch+pad pipeline that
    internally calls ``render_preview_data_url``), call ``fn`` directly
    rather than dispatching to the pool again. Otherwise nested calls
    consume two pool slots per request and can deadlock once the pool's
    4 slots are saturated by the burst.
    """
    if getattr(_in_pool_thread, "active", False):
        return fn(*args, **kwargs)

    def _marked_call():
        _in_pool_thread.active = True
        try:
            return fn(*args, **kwargs)
        finally:
            _in_pool_thread.active = False

    if _HAVE_GEVENT_POOL:
        # gevent.threadpool.ThreadPool.apply blocks the calling greenlet
        # but yields to the gevent hub so other greenlets keep running.
        return _PREVIEW_POOL.apply(_marked_call)
    # Fallback path (no gevent, e.g. unit tests): stdlib Future.result.
    return _PREVIEW_POOL.submit(_marked_call).result()


def render_preview_data_url(
    blob: bytes,
    aspect: str,
    fill_mode: str,
    color: str,
) -> str:
    """Pad ``blob`` for a Kobo preview and return a base64 data URL.

    Used by the cover-picker page (issue #84) to show users what each
    candidate cover will look like on a Kobo device without a full
    apply-and-sync round trip. Inputs map 1:1 to admin settings:
    ``aspect`` is a preset key or "WxH"; ``fill_mode`` is one of the
    five FILL_MODES; ``color`` is a hex string used only when
    ``fill_mode == "manual"``.

    Always returns a JPEG data URL — if the padding pipeline no-ops
    (Wand missing, source already on-target, decode failure), the
    URL still wraps the original bytes so the caller can swap an
    ``<img src>`` unconditionally.

    Concurrent Wand work is bounded by ``_PREVIEW_POOL`` (4 OS threads,
    gevent-aware) so a single user's burst doesn't starve other endpoints
    while still getting real parallelism from ImageMagick's GIL-releasing
    C calls — and the calling greenlet yields the gevent loop instead of
    blocking it.
    """
    settings = CoverPreviewSettings(
        enabled=True,
        target_aspect=aspect or "",
        fill_mode=fill_mode or DEFAULT_FILL_MODE,
        manual_color=color or "",
    )
    if blob:
        padded = _run_in_pool(pad_blob, blob, settings)
    else:
        padded = b""
    payload = padded if padded else (blob or b"")
    encoded = base64.b64encode(payload).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def pad_path_to_cache(
    src_path: str,
    cache_dir: str,
    cache_filename: str,
    settings: CoverPreviewSettings,
) -> Optional[str]:
    """Read src_path, pad, write to cache_dir/cache_filename. Returns the
    cache file path on success (or if the cache hit already exists), None
    on failure or if padding is disabled.

    Caller is responsible for choosing a `cache_filename` that encodes the
    source mtime + settings.settings_hash() so cache invalidation is
    automatic.
    """
    if not use_IM or not settings.enabled or not src_path:
        return None
    if not os.path.isfile(src_path):
        return None

    target = os.path.join(cache_dir, cache_filename)
    if os.path.isfile(target):
        return target

    try:
        with open(src_path, "rb") as fh:
            blob = fh.read()
    except OSError as ex:
        log.warning("cover_preview: cannot read %s: %s", src_path, ex)
        return None

    # This is the cache-miss boundary: pad_blob is pure bytes/settings work,
    # but Wand's decode/composite/JPEG encode blocks the gevent hub while its
    # C calls run. File/path/cache decisions stay on the caller greenlet.
    padded = parallel.run_blocking(lambda: pad_blob(blob, settings))
    if padded is blob:
        # No-op padding: just symlink-equivalent (copy) so the caller can
        # serve from the cache path without branching. Cheap + simple.
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(target, "wb") as out_fh:
                out_fh.write(blob)
            return target
        except OSError as ex:
            log.warning("cover_preview: cannot write passthrough %s: %s", target, ex)
            return None

    try:
        os.makedirs(cache_dir, exist_ok=True)
        # Atomic-ish: write to .tmp then rename so a partial write never
        # serves to a Kobo mid-sync.
        tmp = target + ".tmp"
        with open(tmp, "wb") as out_fh:
            out_fh.write(padded)
        os.replace(tmp, target)
        return target
    except OSError as ex:
        log.warning("cover_preview: cannot write padded %s: %s", target, ex)
        return None


def cache_filename_for(book_uuid: str, resolution, src_mtime: int, settings: CoverPreviewSettings) -> str:
    """Deterministic cache filename. Encodes everything that could change
    the rendered output."""
    # TODO(Task 5 cleanup): rename cache prefix from `kobopad-` to a
    # device-neutral name (e.g. `preview-`) when cover_padding.py is
    # deleted. Doing it then bundles the cache-invalidation event with the
    # module rename, rather than splitting it across two releases.
    return "kobopad-{uuid}-{res}-{mtime}-{hash}.jpg".format(
        uuid=book_uuid,
        res=int(resolution) if resolution else 0,
        mtime=int(src_mtime),
        hash=settings.settings_hash(),
    )
