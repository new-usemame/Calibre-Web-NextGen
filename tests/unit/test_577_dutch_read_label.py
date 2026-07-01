"""Regression tests for #577 — in Dutch the new-UI "open the reader" button read
"Gelezen" (past participle, "has been read") because it reused the msgid "Read",
whose nl translation is the read-*status* label "Gelezen" (correct there). The
button now uses a distinct msgid "Read now" ("Nu lezen"), and the already-read
toggle reuses "Read" (the correct status word) + a separate ✓ instead of the
untranslated "Read ✓".
"""
import pathlib

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.unit
def test_nl_catalog_disambiguates_read_verb_from_status():
    from cps.api.i18n import _load_catalog
    cat = _load_catalog("nl")
    # The reader-open verb and the read-status label are now different strings.
    assert cat.get("Read now") == "Nu lezen"
    assert cat.get("Read") == "Gelezen"


@pytest.mark.unit
def test_bookdetail_uses_disambiguated_msgids():
    src = (_FE / "pages" / "BookDetail.tsx").read_text()
    assert "t('Read now')" in src, "reader-open button must use the 'Read now' msgid"
    # The old ambiguous/untranslated forms are gone.
    assert "t('Read ✓')" not in src
    # The already-read state reuses the status word 'Read' (→ Gelezen) + a ✓.
    assert "`${t('Read')} ✓`" in src


@pytest.mark.unit
def test_read_now_in_pot_template():
    """The new msgid is tracked in the POT so msgmerge propagates it to locales."""
    pot = (pathlib.Path(__file__).resolve().parents[2] / "messages.pot").read_text()
    assert 'msgid "Read now"' in pot
