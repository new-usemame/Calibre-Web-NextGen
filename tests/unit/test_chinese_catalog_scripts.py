"""Keep a conservative set of wrong-script characters out of Chinese catalogs.

PR #2187 supplied Traditional Chinese text containing 签 instead of 簽. Gettext
accepts either codepoint, so compilation cannot detect this translation defect.

Provenance: the ten pairs below were checked against OpenCC's STCharacters.txt
and TSCharacters.txt on 2026-09-08:
https://github.com/BYVoid/OpenCC/tree/master/data/dictionary
The inspected STCharacters.txt SHA256 was
 a0ca1601c70648cf48b33c3c6210ccbecc5c7eead4b4c3daf76587ba2c03582b.
This is a manually reviewed subset of modern script-specific forms, not every
conversion key: a conversion proposal does NOT prove a character is invalid in
the source script. Shared forms (表, 系, 社, 角, 精, 除, 入, 文) and valid Taiwan
forms (台, 群) are deliberately excluded. Every included pair has different
codepoints; the Simplified member is not standard Traditional Taiwan spelling.
签 can become 籤 or 簽 depending on meaning; 簽 is correct for the reported 簽到.

The reverse subset was also measured clean across zh_Hans_CN before enabling
that direction. These ten pairs are an intentionally incomplete guard, not a
language detector or an automatic converter. Extend only with equally certain
pairs and review both catalogs for false positives. The test is fully offline.
"""

from pathlib import Path

import polib
import pytest


TRANSLATIONS = Path(__file__).resolve().parents[2] / "cps" / "translations"
SIMPLIFIED_TO_TRADITIONAL = {
    "签": "簽",
    "书": "書",
    "读": "讀",
    "设": "設",
    "载": "載",
    "删": "刪",
    "译": "譯",
    "错": "錯",
    "简": "簡",
    "体": "體",
}


@pytest.mark.parametrize("locale", ["zh_Hant_TW", "zh_Hans_CN"])
def test_chinese_catalog_uses_expected_script(locale):
    replacements = SIMPLIFIED_TO_TRADITIONAL
    expected_script = "Traditional"
    if locale == "zh_Hans_CN":
        replacements = {v: k for k, v in replacements.items()}
        expected_script = "Simplified"

    catalogs = sorted((TRANSLATIONS / locale).rglob("*.po"))
    assert catalogs, f"No PO catalogs found for {locale}"
    violations = []
    for catalog in catalogs:
        # Include fuzzy drafts and obsolete entries so later reuse stays safe.
        for entry in polib.pofile(str(catalog)):
            fields = {"msgstr": entry.msgstr}
            fields.update({f"msgstr[{i}]": s for i, s in entry.msgstr_plural.items()})
            for field, text in fields.items():
                for character in sorted(set(text) & replacements.keys()):
                    violations.append(
                        f"{catalog.relative_to(TRANSLATIONS)}: "
                        f"msgid={entry.msgid!r}, msgctxt={entry.msgctxt!r}, "
                        f"{field}: {character!r} (U+{ord(character):04X}) "
                        f"requires {expected_script} counterpart "
                        f"{replacements[character]!r}"
                    )
    assert not violations, "Wrong-script characters in translations:\n" + "\n".join(violations)
