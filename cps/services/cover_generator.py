# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Render a typographic cover from a book's own metadata.

A book with no cover is not a rare edge case — public-domain EPUBs, personal
scans and most converted documents arrive without one, and until now they all
showed the same grey ``static/generic_cover.svg`` placeholder in every grid.
This module turns the book's title, series and authors into a real cover the
reader can tell apart at a glance, and the cover picker offers it as one more
source beside the online providers.

Two renderers, one contract
---------------------------
The preferred renderer is Calibre's own ``calibre.ebooks.covers.generate_cover``
— the same engine behind "Generate cover" in Calibre's metadata editor — driven
through ``calibre-debug -e scripts/calibre_generate_cover.py`` because the
application's interpreter has neither ``calibre`` nor Qt.  Where Calibre is not
installed (unit tests, a stripped image) a Pillow renderer honours the identical
preset contract, so the catalogue, the API and the tests never depend on Calibre
being present.  ``render()`` reports which renderer produced the bytes; the two
are deliberately not pixel-identical and nothing compares them.

The design vocabulary is three orthogonal choices — a colour scheme, a font
family and a layout — plus named presets that pick a pleasing combination of the
three.  A preset is a starting point (the admin default is stored as one, so an
unknown id degrades to the default rather than breaking every render), while an
explicitly supplied scheme/font/layout is closed-enum user input and an unknown
value is refused.

Client pixels are never trusted: the SPA sends preset and option *ids*, and both
the preview and the apply path re-render server-side from the book's own stored
metadata.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from .. import logger

log = logger.create()


class CoverGenerationError(Exception):
    """A generated cover could not be produced, with a reason for the caller."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

# Colours are bare hex without '#': Calibre's ``theme_to_colors`` prepends one,
# and a '#'-prefixed value degrades silently to its greyscale fallback.
#   color1          page background
#   color2          the accent block / banner
#   contrast_color1 text drawn on color1
#   contrast_color2 text drawn on color2
COLOR_SCHEMES: dict[str, dict] = {
    "ink": {
        "label": "Ink on cream",
        "color1": "f4efe3", "color2": "1f3a5f",
        "contrast_color1": "1f3a5f", "contrast_color2": "f4efe3",
    },
    "meadow": {
        "label": "Meadow green",
        "color1": "eef4e6", "color2": "3f6b3a",
        "contrast_color1": "24451f", "contrast_color2": "f2f7ec",
    },
    "ember": {
        "label": "Ember red",
        "color1": "fff3e6", "color2": "c0392b",
        "contrast_color1": "7a2d12", "contrast_color2": "fff3e6",
    },
    "slate": {
        "label": "Slate grey",
        "color1": "e9ecef", "color2": "343a40",
        "contrast_color1": "212529", "contrast_color2": "f8f9fa",
    },
    "plum": {
        "label": "Plum violet",
        "color1": "f3ecf7", "color2": "5b2c6f",
        "contrast_color1": "3d1e4a", "contrast_color2": "f7f0fa",
    },
}

# ``calibre`` is the family name Calibre's font scanner resolves; the bundled
# Liberation faces ship with every Calibre build. ``pil`` lists file stems the
# Pillow fallback looks for, most-wanted first.
FONT_FAMILIES: dict[str, dict] = {
    "serif": {
        "label": "Serif",
        "calibre": "Liberation Serif",
        "pil": ("LiberationSerif-Regular", "DejaVuSerif", "Georgia", "Times New Roman"),
        "pil_bold": ("LiberationSerif-Bold", "DejaVuSerif-Bold", "Georgia Bold"),
    },
    "sans": {
        "label": "Sans-serif",
        "calibre": "Liberation Sans",
        "pil": ("LiberationSans-Regular", "DejaVuSans", "Arial", "Helvetica"),
        "pil_bold": ("LiberationSans-Bold", "DejaVuSans-Bold", "Arial Bold"),
    },
}

LAYOUTS: dict[str, dict] = {
    "blocks": {"label": "Blocks — title above, authors on a colour band", "calibre_style": "Blocks"},
    "banner": {"label": "Banner — title on a ribbon near the top", "calibre_style": "Banner"},
    "ornamental": {"label": "Ornamental — title inside a decorative frame", "calibre_style": "Ornamental"},
}

PRESETS: dict[str, dict] = {
    "classic": {"label": "Classic", "scheme": "ink", "font": "serif", "layout": "blocks"},
    "meadow": {"label": "Meadow", "scheme": "meadow", "font": "serif", "layout": "banner"},
    "ember": {"label": "Ember", "scheme": "ember", "font": "sans", "layout": "blocks"},
    "slate": {"label": "Slate", "scheme": "slate", "font": "sans", "layout": "ornamental"},
    "plum": {"label": "Plum", "scheme": "plum", "font": "serif", "layout": "ornamental"},
}

DEFAULT_PRESET = "classic"

# 2:3, because that is what every cover frame in the app is: the picker's current
# cover card, the candidate grid and the compare modal all use aspect-ratio 2/3
# with object-fit: contain. Calibre's own default is 3:4, and rendering at it made
# a freshly applied cover letterbox with dark bars the moment it landed in the
# card the user checks it in. Previews render at half the applied size so the
# round trip stays interactive; both stay inside the clamp below.
APPLY_WIDTH, APPLY_HEIGHT = 1200, 1800
PREVIEW_WIDTH, PREVIEW_HEIGHT = 600, 900
MIN_DIMENSION, MAX_DIMENSION = 200, 2400

# A generated cover is a few hundred KB of flat colour and text. Anything past
# this is a runaway renderer, not a cover, and is refused rather than stored.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

_DEFAULT_TIMEOUT_SECONDS = 25.0


@dataclass(frozen=True)
class CoverSpec:
    """A fully resolved rendering request — no ids left to look up."""

    scheme: str
    font: str
    layout: str
    width: int = APPLY_WIDTH
    height: int = APPLY_HEIGHT

    @property
    def colors(self) -> dict:
        entry = COLOR_SCHEMES[self.scheme]
        return {key: entry[key] for key in
                ("color1", "color2", "contrast_color1", "contrast_color2")}

    def to_dict(self) -> dict:
        return {"scheme": self.scheme, "font": self.font, "layout": self.layout,
                "width": self.width, "height": self.height}


@dataclass(frozen=True)
class BookCoverMeta:
    """The book text a generated cover is made of."""

    title: str
    authors: Sequence[str] = ()
    series: Optional[str] = None
    series_index: Optional[float] = None


@dataclass(frozen=True)
class RenderedCover:
    data: bytes
    renderer: str
    spec: CoverSpec


def _clamp_dimension(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(MIN_DIMENSION, min(MAX_DIMENSION, number))


def resolve_spec(preset: Optional[str] = None,
                 scheme: Optional[str] = None,
                 font: Optional[str] = None,
                 layout: Optional[str] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None) -> CoverSpec:
    """Turn a preset id plus optional overrides into a complete ``CoverSpec``.

    An unknown *preset* falls back to :data:`DEFAULT_PRESET`: the library default
    is persisted as a preset id, so a preset removed in a later release must not
    take every automatic cover down with it. An unknown *scheme*, *font* or
    *layout* is refused — those arrive per request from a closed enum the client
    was handed, so a value outside it is a bug or a probe, not stale state.
    """
    base = PRESETS.get(preset or DEFAULT_PRESET)
    if base is None:
        log.info("cover_generator: unknown preset %r, using %s", preset, DEFAULT_PRESET)
        base = PRESETS[DEFAULT_PRESET]

    chosen_scheme = scheme or base["scheme"]
    chosen_font = font or base["font"]
    chosen_layout = layout or base["layout"]
    if chosen_scheme not in COLOR_SCHEMES:
        raise CoverGenerationError("unknown_scheme", "Unknown colour scheme: %s" % chosen_scheme)
    if chosen_font not in FONT_FAMILIES:
        raise CoverGenerationError("unknown_font", "Unknown font family: %s" % chosen_font)
    if chosen_layout not in LAYOUTS:
        raise CoverGenerationError("unknown_layout", "Unknown layout: %s" % chosen_layout)

    return CoverSpec(
        scheme=chosen_scheme,
        font=chosen_font,
        layout=chosen_layout,
        width=_clamp_dimension(width, APPLY_WIDTH),
        height=_clamp_dimension(height, APPLY_HEIGHT),
    )


def catalogue() -> dict:
    """The design vocabulary, shaped for the SPA's designer panel.

    Labels are English source strings: the SPA translates them through the same
    msgid catalogue as the rest of its chrome (see ``cps/spa_strings.py``).
    """
    return {
        "default_preset": DEFAULT_PRESET,
        "presets": [
            {"id": key, "label": value["label"], "scheme": value["scheme"],
             "font": value["font"], "layout": value["layout"]}
            for key, value in PRESETS.items()
        ],
        "schemes": [
            {"id": key, "label": value["label"],
             "swatch": ["#" + value["color1"], "#" + value["color2"]]}
            for key, value in COLOR_SCHEMES.items()
        ],
        "fonts": [{"id": key, "label": value["label"]} for key, value in FONT_FAMILIES.items()],
        "layouts": [{"id": key, "label": value["label"]} for key, value in LAYOUTS.items()],
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def calibre_debug_path(binaries_dir: str = "") -> str:
    """Absolute path of a usable ``calibre-debug``, or "" when there is none.

    The configured binaries directory wins over ``PATH`` for the same reason the
    converter path does: an admin who points CWNG at one Calibre install must not
    get a different one here.
    """
    if binaries_dir:
        candidate = os.path.join(binaries_dir, "calibre-debug")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    override = os.environ.get("CWNG_CALIBRE_DEBUG_PATH", "")
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else ""
    return shutil.which("calibre-debug") or ""


def _helper_script_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "calibre_generate_cover.py",
    )


def _timeout_seconds() -> float:
    raw = os.environ.get("CWNG_COVER_GENERATOR_TIMEOUT_SECONDS", "")
    try:
        value = float(raw)
        return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS


def _preferred_renderer() -> str:
    """"calibre", "pil" or "auto" — the env knob exists so tests can pin one."""
    value = (os.environ.get("CWNG_COVER_GENERATOR_RENDERER") or "auto").strip().lower()
    return value if value in ("calibre", "pil", "auto") else "auto"


def renderer_availability(binaries_dir: str = "") -> dict:
    """Which renderers this installation can actually use, right now."""
    preferred = _preferred_renderer()
    calibre_ok = bool(calibre_debug_path(binaries_dir)) and preferred != "pil"
    pil_ok = _pil_available() and preferred != "calibre"
    return {
        "calibre": calibre_ok,
        "pil": pil_ok,
        "available": bool(calibre_ok or pil_ok),
        "renderer": "calibre" if calibre_ok else ("pil" if pil_ok else None),
    }


_COMPLEX_SCRIPT_WARNED = [False]


def _warn_once_if_no_complex_shaping(text: str) -> None:
    """Say so when Pillow cannot shape the script this title is written in.

    Arabic, Hebrew, Devanagari and the Indic scripts need HarfBuzz (Pillow's
    optional ``raqm`` layout engine) to join and reorder their glyphs; without it
    Pillow draws them unjoined and left-to-right, which is unreadable rather than
    merely plain. Calibre's renderer shapes them correctly, so this only bites a
    Calibre-less installation — and it is a log line, not a refusal: a wrong-
    looking cover the admin has been told about beats no cover with no reason.
    """
    if _COMPLEX_SCRIPT_WARNED[0] or not text:
        return
    if not any("\u0590" <= character <= "\u1cff" or "\ufb00" <= character <= "\ufdff"
               for character in text):
        return
    try:
        from PIL import features
        if features.check("raqm"):
            return
    except ImportError:  # pragma: no cover - defensive
        return
    _COMPLEX_SCRIPT_WARNED[0] = True
    log.warning(
        "cover_generator: this Pillow build has no raqm/HarfBuzz support, so generated "
        "covers for titles in Arabic, Hebrew or an Indic script will render unshaped. "
        "Install Calibre (its renderer shapes them correctly) or a Pillow built with raqm."
    )


def _pil_available() -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError:  # pragma: no cover - Pillow is a hard runtime dependency
        return False
    return True


def render(meta: BookCoverMeta, spec: CoverSpec, binaries_dir: str = "") -> RenderedCover:
    """Render ``meta`` as ``spec`` and return the JPEG bytes.

    Calibre first when it is installed; a Calibre failure (missing Qt, a timeout,
    a crash) falls through to Pillow rather than failing the request, because a
    plainer cover is a better answer than none. When neither renderer can run,
    ``CoverGenerationError('unavailable')`` says so honestly instead of writing a
    placeholder the caller would mistake for a cover.
    """
    preferred = _preferred_renderer()
    errors = []

    if preferred in ("auto", "calibre"):
        binary = calibre_debug_path(binaries_dir)
        if binary:
            try:
                return RenderedCover(_render_with_calibre(binary, meta, spec), "calibre", spec)
            except CoverGenerationError as error:
                errors.append("calibre: %s" % error.message)
                log.warning("cover_generator: calibre renderer failed (%s); falling back", error.message)
        else:
            errors.append("calibre: calibre-debug not found")
        if preferred == "calibre":
            raise CoverGenerationError("unavailable", "; ".join(errors))

    if preferred in ("auto", "pil"):
        if _pil_available():
            return RenderedCover(_render_with_pil(meta, spec), "pil", spec)
        errors.append("pil: Pillow not importable")

    raise CoverGenerationError("unavailable", "; ".join(errors) or "no renderer available")


def render_data_url(meta: BookCoverMeta, spec: CoverSpec, binaries_dir: str = "") -> tuple[str, str]:
    """``(data_url, renderer)`` for a preview the browser can drop into an <img>."""
    rendered = render(meta, spec, binaries_dir=binaries_dir)
    encoded = base64.b64encode(rendered.data).decode("ascii")
    return "data:image/jpeg;base64," + encoded, rendered.renderer


# ---- Calibre ---------------------------------------------------------------

def _render_with_calibre(binary: str, meta: BookCoverMeta, spec: CoverSpec) -> bytes:
    script = _helper_script_path()
    if not os.path.isfile(script):  # pragma: no cover - packaging error
        raise CoverGenerationError("helper_missing", "helper script not found: %s" % script)

    request = json.dumps({
        "title": meta.title or "",
        "authors": list(meta.authors or ()),
        "series": meta.series or None,
        "series_index": meta.series_index,
        "spec": {
            "width": spec.width,
            "height": spec.height,
            "style": LAYOUTS[spec.layout]["calibre_style"],
            "font_family": FONT_FAMILIES[spec.font]["calibre"],
            "colors": spec.colors,
        },
    })

    env = os.environ.copy()
    # No display in a container, and none on a headless test host either.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        completed = subprocess.run(
            [binary, "-e", script],
            input=request,
            capture_output=True,
            text=True,
            timeout=_timeout_seconds(),
            env=env,
            # Its own session so a wedged Qt child tree dies with the parent kill.
            start_new_session=(os.name != "nt"),
        )
    except subprocess.TimeoutExpired:
        raise CoverGenerationError("timeout", "calibre-debug timed out after %ss" % _timeout_seconds())
    except OSError as error:
        raise CoverGenerationError("spawn_failed", str(error))

    payload = None
    for line in reversed((completed.stdout or "").splitlines()):
        if line.startswith("CWNG_COVER_RESULT="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except ValueError as error:
                raise CoverGenerationError("bad_result", "unparseable result line: %s" % error)
            break
    if payload is None:
        raise CoverGenerationError(
            "no_result",
            "helper produced no result (exit %s): %s" % (
                completed.returncode, (completed.stderr or "").strip()[:400]),
        )
    if not payload.get("ok"):
        raise CoverGenerationError("render_failed", str(payload.get("error") or "unknown error"))

    try:
        data = base64.b64decode(payload.get("data") or "", validate=True)
    except (ValueError, TypeError) as error:
        raise CoverGenerationError("bad_result", "result payload is not base64: %s" % error)
    if not data:
        raise CoverGenerationError("bad_result", "result payload is empty")
    if len(data) > MAX_OUTPUT_BYTES:
        raise CoverGenerationError(
            "too_large", "generated cover is %s bytes, over the %s byte cap" % (
                len(data), MAX_OUTPUT_BYTES))
    return data


# ---- Pillow ----------------------------------------------------------------

_FONT_SEARCH_DIRS = (
    "/usr/share/fonts", "/usr/local/share/fonts", "/usr/share/fonts/truetype",
    "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
)


def _find_font_file(stems: Sequence[str]) -> Optional[str]:
    """First readable .ttf/.otf whose filename stem matches, searched in order."""
    wanted = [stem.lower() for stem in stems]
    found: dict[str, str] = {}
    for root in _FONT_SEARCH_DIRS:
        if not os.path.isdir(root):
            continue
        for directory, _subdirs, filenames in os.walk(root):
            for filename in filenames:
                stem, extension = os.path.splitext(filename)
                if extension.lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                key = stem.lower()
                if key in wanted and key not in found:
                    found[key] = os.path.join(directory, filename)
        if len(found) == len(wanted):
            break
    for stem in wanted:
        if stem in found:
            return found[stem]
    return None


def _load_font(stems: Sequence[str], size: int):
    from PIL import ImageFont

    path = _find_font_file(stems)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):  # pragma: no cover - unreadable font file
            pass
    try:
        # Pillow >= 10.1 scales its built-in face; older ones ignore the size and
        # still render legible (if small) text rather than failing the render.
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def _hex_to_rgb(value: str) -> tuple:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list:
    """Greedy word wrap measured with the real font, truncated with an ellipsis."""
    words = (text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        trial = current + " " + word
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = (last + "…") if last else "…"
    return lines


def _draw_centered(draw, lines, font, fill, top: int, width: int, line_gap: int) -> int:
    """Draw ``lines`` centred on ``width``; return the y just below the block."""
    y = top
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        line_height = box[3] - box[1]
        draw.text(((width - (box[2] - box[0])) / 2 - box[0], y - box[1]), line, font=font, fill=fill)
        y += line_height + line_gap
    return y


def _render_with_pil(meta: BookCoverMeta, spec: CoverSpec) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    width, height = spec.width, spec.height
    colors = spec.colors
    background = _hex_to_rgb(colors["color1"])
    accent = _hex_to_rgb(colors["color2"])
    on_background = _hex_to_rgb(colors["contrast_color1"])
    on_accent = _hex_to_rgb(colors["contrast_color2"])

    family = FONT_FAMILIES[spec.font]
    title_font = _load_font(family["pil_bold"] + family["pil"], max(16, int(width * 0.095)))
    subtitle_font = _load_font(family["pil"], max(12, int(width * 0.05)))
    footer_font = _load_font(family["pil_bold"] + family["pil"], max(12, int(width * 0.055)))

    _warn_once_if_no_complex_shaping(meta.title)

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    margin = int(width * 0.08)
    text_width = width - 2 * margin

    subtitle = ""
    if meta.series:
        subtitle = meta.series
        if meta.series_index is not None:
            index = meta.series_index
            subtitle += " — %s" % (int(index) if float(index).is_integer() else index)
    authors = ", ".join([a for a in (meta.authors or ()) if a][:2])

    if spec.layout == "blocks":
        band_top = int(height * 0.72)
        draw.rectangle([0, band_top, width, height], fill=accent)
        title_lines = _wrap(draw, meta.title, title_font, text_width, 4)
        y = _draw_centered(draw, title_lines, title_font, on_background,
                           int(height * 0.10), width, int(height * 0.012))
        if subtitle:
            _draw_centered(draw, _wrap(draw, subtitle, subtitle_font, text_width, 2),
                           subtitle_font, on_background, y + int(height * 0.02), width, 6)
        if authors:
            _draw_centered(draw, _wrap(draw, authors, footer_font, text_width, 2),
                           footer_font, on_accent, band_top + int(height * 0.10), width, 8)

    elif spec.layout == "banner":
        banner_top, banner_bottom = int(height * 0.06), int(height * 0.34)
        draw.rectangle([margin // 2, banner_top, width - margin // 2, banner_bottom], fill=accent)
        title_lines = _wrap(draw, meta.title, title_font, text_width - margin, 3)
        _draw_centered(draw, title_lines, title_font, on_accent,
                       banner_top + int(height * 0.04), width, int(height * 0.010))
        if subtitle:
            _draw_centered(draw, _wrap(draw, subtitle, subtitle_font, text_width, 2),
                           subtitle_font, on_background, banner_bottom + int(height * 0.05), width, 6)
        if authors:
            _draw_centered(draw, _wrap(draw, authors, footer_font, text_width, 2),
                           footer_font, on_background, int(height * 0.84), width, 8)

    else:  # ornamental
        outer = margin // 2
        draw.rectangle([outer, outer, width - outer, height - outer], outline=accent,
                       width=max(3, int(width * 0.012)))
        inner = outer + max(8, int(width * 0.03))
        draw.rectangle([inner, inner, width - inner, height - inner], outline=accent,
                       width=max(2, int(width * 0.004)))
        title_lines = _wrap(draw, meta.title, title_font, text_width - margin, 4)
        y = _draw_centered(draw, title_lines, title_font, on_background,
                           int(height * 0.20), width, int(height * 0.012))
        if subtitle:
            _draw_centered(draw, _wrap(draw, subtitle, subtitle_font, text_width - margin, 2),
                           subtitle_font, on_background, y + int(height * 0.02), width, 6)
        if authors:
            draw.rectangle([inner, int(height * 0.74), width - inner, int(height * 0.745)], fill=accent)
            _draw_centered(draw, _wrap(draw, authors, footer_font, text_width - margin, 2),
                           footer_font, on_background, int(height * 0.79), width, 8)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    data = buffer.getvalue()
    if len(data) > MAX_OUTPUT_BYTES:  # pragma: no cover - flat colour never gets here
        raise CoverGenerationError("too_large", "generated cover exceeds the size cap")
    return data


# ---------------------------------------------------------------------------
# Settings, shared by the app and the standalone ingest/enforcer scripts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratorSettings:
    auto_enabled: bool
    default_preset: str


def settings_from_app_db(app_db_path: str) -> GeneratorSettings:
    """Read the two library settings straight out of ``app.db``.

    ``ingest_processor.py`` and ``cover_enforcer.py`` run as standalone
    processes under s6 with no Flask application, and both already read this
    table with sqlite3. A missing column (an installation that has not migrated
    yet) reads as "feature off" rather than raising.
    """
    try:
        connection = sqlite3.connect(app_db_path, timeout=30)
    except sqlite3.Error as error:  # pragma: no cover - defensive
        log.warning("cover_generator: could not open %s: %s", app_db_path, error)
        return GeneratorSettings(False, DEFAULT_PRESET)
    try:
        row = connection.execute(
            "SELECT config_cover_generator_auto_enabled, config_cover_generator_default_preset "
            "FROM settings"
        ).fetchone()
    except sqlite3.Error:
        return GeneratorSettings(False, DEFAULT_PRESET)
    finally:
        connection.close()
    if not row:
        return GeneratorSettings(False, DEFAULT_PRESET)
    return GeneratorSettings(bool(row[0]), str(row[1] or DEFAULT_PRESET))


def generate_cover_file(destination: str, meta: BookCoverMeta,
                        preset: Optional[str] = None,
                        binaries_dir: str = "") -> bool:
    """Write a generated ``cover.jpg`` at *destination*. False if it exists.

    Used by the ingest and enforcement paths, which own the ``has_cover`` flag
    themselves. Refusing to overwrite an existing file is what keeps "generate a
    cover for books that arrive with none" from ever touching a book that has
    one — including on a re-run over the same library.
    """
    if os.path.exists(destination):
        return False
    spec = resolve_spec(preset=preset)
    rendered = render(meta, spec, binaries_dir=binaries_dir)
    directory = os.path.dirname(destination)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    # Write a sibling and rename so a reader never sees a half-written cover.
    staging = destination + ".cwng-generating"
    with open(staging, "wb") as handle:
        handle.write(rendered.data)
    os.replace(staging, destination)
    log.info("cover_generator: wrote generated cover (%s renderer) to %s",
             rendered.renderer, destination)
    return True
