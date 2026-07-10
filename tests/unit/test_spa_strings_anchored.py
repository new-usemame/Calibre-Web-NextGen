# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gate: every SPA t('literal') is anchored in cps/spa_strings.py (issue #719).

The React SPA translates via ``t('English source')`` resolved against a catalog
built from the .po files (cps/api/i18n.py). But ``pybabel extract`` only scans
Python/Jinja (babel.cfg), never .tsx, so an SPA-only string that isn't
re-declared in cps/spa_strings.py is dropped from messages.pot and renders in
English in every locale — exactly the #719 report (menu items untranslated
despite a complete Russian .po).

These tests fail the moment a frontend t() literal is added without a matching
anchor, so the drift that caused #719 cannot silently return. The extraction
logic is imported from scripts/extract_spa_strings.py so the gate and the
generator can never disagree.
"""
import importlib.util
import os

import pytest


pytestmark = pytest.mark.unit

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_REPO, "scripts", "extract_spa_strings.py")


def _load_extractor():
    spec = importlib.util.spec_from_file_location("extract_spa_strings", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


extractor = _load_extractor()


def test_every_spa_t_literal_is_anchored():
    """No SPA t() literal may be missing from cps/spa_strings.py.

    Regenerate with: python scripts/extract_spa_strings.py --write
    """
    missing = extractor.missing_anchors()
    assert missing == [], (
        f"{len(missing)} SPA t() literal(s) are not anchored in cps/spa_strings.py "
        f"and will render untranslated (issue #719). Run "
        f"`python scripts/extract_spa_strings.py --write`. Missing: {missing[:20]}"
    )


@pytest.mark.parametrize("msgid", ["Table view", "Smart shelves"])
def test_reporter_menu_strings_anchored(msgid):
    """The exact sidebar menu items #719 reported as untranslated stay anchored."""
    assert msgid in extractor.parse_anchored(), (
        f"{msgid!r} (a sidebar menu item from issue #719) must remain anchored so "
        "it is extracted into messages.pot and can be translated."
    )


def test_autogen_markers_present():
    """The AUTOGEN block markers must survive so --write stays idempotent and the
    generated section can't be silently collapsed into hand edits."""
    with open(extractor.SPA_STRINGS, encoding="utf-8") as fh:
        content = fh.read()
    assert extractor.AUTOGEN_BEGIN in content
    assert extractor.AUTOGEN_END in content


def test_extractor_finds_known_frontend_call():
    """Sanity-pin the t()-literal scanner against a string we know is in the SPA,
    so a regex regression that silently matches nothing is caught."""
    keys = extractor.extract_frontend_keys()
    assert "Table view" in keys
    assert any(f.endswith(".tsx") for f in keys["Table view"])
