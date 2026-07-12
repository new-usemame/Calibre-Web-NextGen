#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep cps/spa_strings.py in sync with the SPA's translatable strings.

The React SPA translates through ``t('English source')`` (frontend/src/lib/
i18n.tsx): the English string IS the gettext msgid, resolved against a per-locale
catalog built from the same .po files the classic UI uses (cps/api/i18n.py). But
``pybabel extract`` only scans Python + Jinja (babel.cfg), never .tsx — so a
string used ONLY in the SPA is dropped from messages.pot on the next re-extract,
msgmerge marks its translation obsolete, and the SPA silently falls back to
English. cps/spa_strings.py exists to re-declare those SPA-only msgids in a file
babel DOES scan, so they survive extraction.

Historically that file was hand-maintained ("add a msgid the moment you add it in
the frontend"), which drifted: issue #719 — dozens of SPA strings (whole Admin,
CoverPicker, AdvancedSearch, EditBook … surfaces) were never anchored, so Russian
(and every locale) rendered them in English despite a complete .po.

This module is the single source of truth for that sync. It parses every
``t('literal')`` call and every static ``label: 'literal'`` data entry out of
the frontend, plus every ``_('literal')`` anchor out of spa_strings.py, and
either:

  * ``--check`` (default): exits non-zero listing any frontend literal that is not
    anchored — this is the CI gate (tests/unit/test_spa_strings_anchored.py) that
    prevents the drift from ever recurring, OR
  * ``--write``: regenerates the AUTOGEN block in spa_strings.py so every current
    frontend literal is anchored.

Static ``label`` entries are included because the SPA deliberately keeps menu,
button-grid, filter, and sort copy in data structures before rendering it with
``t(label)``. That pattern caused the residual #719 gap even after all direct
``t('literal')`` calls were gated. Other dynamic keys still need an explicit
anchor.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
FRONTEND_SRC = os.path.join(_REPO, "frontend", "src")
SPA_STRINGS = os.path.join(_REPO, "cps", "spa_strings.py")

AUTOGEN_BEGIN = "# ==== BEGIN AUTOGEN (scripts/extract_spa_strings.py --write) ===="
AUTOGEN_END = "# ==== END AUTOGEN ===="

# t('...') / t("...") / t(`...`) where the call is a real translate call (the char
# before `t` is not an identifier char, so format()/at()/etc. don't match) and the
# template-literal form carries no ${} interpolation (a dynamic key can't be a
# static msgid). Matches an optional second arg (the vars object) via [,)].
_T_CALL = re.compile(
    r"""(?<![A-Za-z0-9_$.])t\(\s*"""
    r"""(?:'((?:[^'\\]|\\.)*)'"""
    r"""|"((?:[^"\\]|\\.)*)\""""
    r"""|`([^`$\\]*)`)\s*[,)]"""
)

# Data-driven UI copy rendered later through t(label). This intentionally keys
# on the conventional property name rather than every TS string literal: the
# latter would pollute the catalogs with routes, API values, CSS keys, and test
# data. Covers both object literals (`label: 'Newest'`) and quoted keys.
_LABEL_PROPERTY = re.compile(
    r"""(?:\blabel\b|['\"]label['\"])\s*:\s*"""
    r"""(?:'((?:[^'\\]|\\.)*)'"""
    r"""|\"((?:[^\"\\]|\\.)*)\""""
    r"""|`([^`$\\]*)`)"""
)

# JS string escapes we care about in UI copy: \' \" \\ \n \t \r and \` — map to the
# runtime value the SPA passes to t(), which is what the msgid must equal.
_JS_UNESCAPE = re.compile(r"\\(.)")
_JS_ESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r"}


def _decode_js(literal: str) -> str:
    return _JS_UNESCAPE.sub(lambda m: _JS_ESCAPE_MAP.get(m.group(1), m.group(1)), literal)


def extract_frontend_keys(frontend_src: str = FRONTEND_SRC) -> dict[str, set[str]]:
    """Return static direct and data-driven gettext keys used by the SPA."""
    keys: dict[str, set[str]] = {}
    for dirpath, _dirs, files in os.walk(frontend_src):
        for fname in files:
            if not fname.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(dirpath, fname)
            try:
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(path, frontend_src)
            for match in _T_CALL.finditer(src):
                raw = match.group(1) or match.group(2) or match.group(3)
                if raw is None or raw == "":
                    continue
                keys.setdefault(_decode_js(raw), set()).add(rel)
            for match in _LABEL_PROPERTY.finditer(src):
                raw = match.group(1) or match.group(2) or match.group(3)
                if raw is None or raw == "":
                    continue
                keys.setdefault(_decode_js(raw), set()).add(rel)
    return keys


def parse_anchored(spa_strings_path: str = SPA_STRINGS) -> set[str]:
    """Return the set of msgids anchored via _('...') in spa_strings.py."""
    with open(spa_strings_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=spa_strings_path)
    anchored: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            anchored.add(node.args[0].value)
    return anchored


def missing_anchors(
    frontend_src: str = FRONTEND_SRC, spa_strings_path: str = SPA_STRINGS
) -> list[str]:
    """Frontend literals not yet anchored, sorted for stable output."""
    anchored = parse_anchored(spa_strings_path)
    keys = extract_frontend_keys(frontend_src)
    return sorted(k for k in keys if k not in anchored)


def _render_autogen(missing: list[str]) -> str:
    lines = [
        AUTOGEN_BEGIN,
        "# Auto-anchored SPA-only msgids — every t('literal') and static label",
        "# property in frontend/src that",
        "# is not already anchored above. Do NOT edit by hand; run",
        "#   python scripts/extract_spa_strings.py --write",
        "# after adding or removing SPA strings. The CI gate",
        "# (tests/unit/test_spa_strings_anchored.py) fails if this drifts.",
    ]
    for key in missing:
        lines.append("_(%s)" % json.dumps(key, ensure_ascii=False))
    lines.append(AUTOGEN_END)
    return "\n".join(lines) + "\n"


def write_autogen(
    frontend_src: str = FRONTEND_SRC, spa_strings_path: str = SPA_STRINGS
) -> int:
    """Regenerate the AUTOGEN block; return count of anchored-in-block strings."""
    # Everything the frontend uses that isn't in the CURATED (non-autogen) part.
    with open(spa_strings_path, encoding="utf-8") as fh:
        content = fh.read()
    if AUTOGEN_BEGIN in content:
        content = content[: content.index(AUTOGEN_BEGIN)].rstrip("\n") + "\n"
    curated = parse_anchored_from_text(content)
    keys = extract_frontend_keys(frontend_src)
    block_keys = sorted(k for k in keys if k not in curated)
    new_content = content.rstrip("\n") + "\n\n\n" + _render_autogen(block_keys)
    with open(spa_strings_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)
    return len(block_keys)


def parse_anchored_from_text(text: str) -> set[str]:
    tree = ast.parse(text)
    anchored: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            anchored.add(node.args[0].value)
    return anchored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail if anchors drift (default)")
    group.add_argument("--write", action="store_true", help="regenerate the AUTOGEN block")
    args = parser.parse_args(argv)

    if args.write:
        n = write_autogen()
        print(f"[spa_strings] wrote AUTOGEN block: {n} anchored msgid(s)")
        return 0

    missing = missing_anchors()
    if missing:
        print(
            f"[spa_strings] {len(missing)} SPA translation key(s) are NOT anchored in "
            "cps/spa_strings.py — they will be dropped from messages.pot and render "
            "in English. Run: python scripts/extract_spa_strings.py --write",
            file=sys.stderr,
        )
        for key in missing:
            print(f"    {key!r}", file=sys.stderr)
        return 1
    print("[spa_strings] OK — every static SPA translation key is anchored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
