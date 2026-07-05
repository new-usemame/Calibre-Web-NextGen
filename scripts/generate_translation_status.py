# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Refresh translation-status tables in the wiki and the repo README.

The wiki page (`Contributing-Translations.md`) gets a full per-language
table with raw counts. The repo README.md gets a compact bar-graph
oriented table that scans well at a glance. Both targets are updated
between explicit comment markers so the rest of the document is left
untouched.

Usage:
    generate_translation_status.py                     # README.md only
    generate_translation_status.py wiki-tmp/page.md    # wiki + README
"""

import polib
from pathlib import Path
import re
import sys

LANGUAGE_NAMES = {
    "ar": "Arabic",
    "cs": "Czech",
    "de": "German",
    "el": "Greek",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "gl": "Galician",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "km": "Khmer",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "pt_BR": "Portuguese (Brazil)",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sv": "Swedish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh_Hans_CN": "Chinese (Simplified, China)",
    "zh_Hant_TW": "Chinese (Traditional, Taiwan)",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
START_MARKER = "<!-- TRANSLATION_STATUS_START -->"
END_MARKER = "<!-- TRANSLATION_STATUS_END -->"


def collect_stats():
    """Return a list of (lang, lang_name, total, translated, fuzzy, percent)
    tuples sorted by completion percentage (highest first), with English
    pinned to the top as the source language."""
    stats = []
    for po_path in sorted(ROOT_DIR.glob("cps/translations/*/LC_MESSAGES/messages.po")):
        lang = po_path.parts[-3]
        po = polib.pofile(str(po_path))
        total = sum(1 for e in po if not e.obsolete)
        translated = sum(1 for e in po if not e.obsolete and e.msgstr.strip())
        fuzzy = sum(1 for e in po if not e.obsolete and "fuzzy" in e.flags)
        percent = round(100 * translated / total, 1) if total else 0.0
        stats.append((lang, LANGUAGE_NAMES.get(lang, lang), total, translated, fuzzy, percent))
    stats.sort(key=lambda r: (-r[5], r[1]))
    return stats


def render_readme_table(stats):
    """Compact table for the repo README. Pure markdown, no images, no
    external services — renders the same on github.com and on a clone."""
    lines = [
        "| Language | Completion | Strings | Fuzzy |",
        "|---|---|---:|---:|",
        "| English (source) | 100% | source | — |",
    ]
    for lang, name, total, translated, fuzzy, percent in stats:
        bar_filled = int(round(percent / 5))
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        lines.append(
            f"| {name} (`{lang}`) | `{bar}` {percent:.0f}% | {translated}/{total} | {fuzzy} |"
        )
    return "\n".join(lines)


def render_wiki_table(stats):
    """Full table for the wiki page. Links each row back to the .po file
    on this repo so contributors can edit directly."""
    lines = [
        "| Language | Total Strings | Untranslated | Completion |",
        "|---|---|---|---|",
    ]
    for lang, name, total, translated, fuzzy, percent in stats:
        untranslated = total - translated
        lines.append(
            f"| {name} ([{lang}](https://github.com/new-usemame/Calibre-Web-NextGen/tree/main/cps/translations/{lang}/LC_MESSAGES)) "
            f"| {total} | {untranslated} | {int(percent)}% |"
        )
    return "\n".join(lines)


def update_between_markers(path: Path, body: str) -> bool:
    """Replace text between START_MARKER and END_MARKER. Returns True if
    file was modified (or markers needed to be inserted), False if the
    file lacked markers AND we're not allowed to invent them.

    A non-existent path has no markers to update, so this returns False
    without raising. main() relies on that to fall through to its
    seed-on-first-run fallback; reading unconditionally here would raise
    FileNotFoundError and defeat it."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = f"{START_MARKER}\n{body}\n{END_MARKER}"
    if START_MARKER in text and END_MARKER in text:
        new_text = re.sub(
            f"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
            block,
            text,
            flags=re.DOTALL,
        )
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            return True
        return False
    return False


def main():
    stats = collect_stats()

    # README.md is always updated.
    readme_path = ROOT_DIR / "README.md"
    if readme_path.exists():
        if update_between_markers(readme_path, render_readme_table(stats)):
            print(f"Updated translation table in {readme_path}.")
        else:
            print(f"No marker block in {readme_path}; skipped.")

    # Wiki page is updated only if a path is supplied.
    if len(sys.argv) > 1:
        wiki_path = Path(sys.argv[1])
        if update_between_markers(wiki_path, render_wiki_table(stats)):
            print(f"Updated translation table in {wiki_path}.")
        else:
            # Fall back to the original "insert after first heading" behaviour
            # so the wiki file gets seeded on first run.
            text = wiki_path.read_text(encoding="utf-8") if wiki_path.exists() else ""
            block = f"{START_MARKER}\n{render_wiki_table(stats)}\n{END_MARKER}"
            if text:
                parts = text.split("\n", 2)
                if len(parts) > 2:
                    text = parts[0] + "\n" + parts[1] + "\n" + block + "\n" + parts[2]
                else:
                    text = text + "\n" + block
            else:
                text = block
            wiki_path.write_text(text, encoding="utf-8")
            print(f"Seeded translation table in {wiki_path}.")


if __name__ == "__main__":
    main()
