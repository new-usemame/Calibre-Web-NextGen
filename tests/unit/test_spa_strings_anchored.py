# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gate: every static SPA translation key is anchored (issue #719).

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
import re
import subprocess
import sys

import pytest


pytestmark = pytest.mark.unit

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_REPO, "scripts", "extract_spa_strings.py")
_FUZZY_SCRIPT = os.path.join(_REPO, "scripts", "check_spa_fuzzy.py")
_USER_NOTICES = os.path.join(_REPO, "frontend", "src", "components", "UserNotices.tsx")

_KOBO_REPAIR_NOTICE_MSGIDS = (
    "We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again.",
    "We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again. If you read it some other way, download it again to get the repaired copy.",
    "We repaired {count} books for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove them from your Kobo and let them download again.",
)

_SUPERSEDED_KOBO_REPAIR_CAVEATS = (
    "Older highlights may still need to be recreated",
    "Highlights you made before the repair may not come back",
    "highlights created before the repair may remain invisible",
    "highlights may not come back",
    "highlights may not return",
)


def _load_extractor():
    spec = importlib.util.spec_from_file_location("extract_spa_strings", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


extractor = _load_extractor()


def test_every_static_spa_translation_key_is_anchored():
    """No direct t() literal or data-driven label may be missing.

    Regenerate with: python scripts/extract_spa_strings.py --write
    """
    missing = extractor.missing_anchors()
    assert missing == [], (
        f"{len(missing)} SPA translation key(s) are not anchored in cps/spa_strings.py "
        f"and will render untranslated (issue #719). Run "
        f"`python scripts/extract_spa_strings.py --write`. Missing: {missing[:20]}"
    )


def test_operator_chosen_kobo_repair_notices_are_exact_and_anchored():
    """#1748's final English is a fixed invariant, not editable chrome.

    The generic extraction gate follows whatever the frontend currently says,
    so it cannot reject a coordinated reword. Pin each exact call and anchor;
    the e2e coverage separately proves each complete sentence is rendered.
    """
    with open(_USER_NOTICES, encoding="utf-8") as source_file:
        frontend = source_file.read()
    with open(extractor.SPA_STRINGS, encoding="utf-8") as source_file:
        anchors = source_file.read()

    for msgid in _KOBO_REPAIR_NOTICE_MSGIDS:
        assert frontend.count(f"t('{msgid}'") == 1, (
            f"operator-chosen Kobo repair notice changed or is not a single t() call: {msgid!r}")
        assert anchors.count(f'_("{msgid}")') == 1, (
            f"operator-chosen Kobo repair notice lacks its exact extraction anchor: {msgid!r}")


def test_operator_chosen_kobo_repair_notices_omit_superseded_caveats():
    """The final #1748 decision deliberately removed every earlier caveat."""
    with open(_USER_NOTICES, encoding="utf-8") as source_file:
        frontend = source_file.read()
    with open(extractor.SPA_STRINGS, encoding="utf-8") as source_file:
        active_copy = frontend + source_file.read()

    for caveat in _SUPERSEDED_KOBO_REPAIR_CAVEATS:
        assert caveat.casefold() not in active_copy.casefold(), (
            f"superseded Kobo repair caveat returned: {caveat!r}")


@pytest.mark.parametrize(
    "msgid",
    [
        "A few random picks from your library",
        "Table view",
        "Smart shelves",
        "Formats",
        "Favorites",
        "Hot",
        "Top Rated",
        "Load more",
        "Full user table & restrictions",
        "Basic configuration",
        "Database & library path",
        "Scheduled tasks",
    ],
)
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


@pytest.mark.parametrize("msgid", ["Formats", "Newest", "Basic configuration"])
def test_extractor_finds_data_driven_labels(msgid):
    """Variable-rendered labels must not escape extraction again (#719/#615)."""
    keys = extractor.extract_frontend_keys()
    assert msgid in keys


def test_accessible_names_and_empty_states_are_not_raw_english_literals():
    """Derive this gate from every TSX file instead of pinning today's file
    list: visible empty states and accessible names are user-facing SPA copy
    and must go through ``t()`` just like ordinary JSX text (#886).
    """
    offenders = []
    for root, _, files in os.walk(extractor.FRONTEND_SRC):
        for filename in files:
            if not filename.endswith(".tsx"):
                continue
            path = os.path.join(root, filename)
            with open(path, encoding="utf-8") as source_file:
                source = source_file.read()
            for pattern in (
                r'aria-label="[A-Za-z][^"]*"',
                r'<EmptyState\b[^>]*\bmessage="[A-Za-z][^"]*"',
            ):
                for match in re.finditer(pattern, source):
                    line = source.count("\n", 0, match.start()) + 1
                    offenders.append(f"{os.path.relpath(path, extractor._REPO)}:{line}: {match.group(0)}")
    assert offenders == []


@pytest.mark.parametrize(
    ("relative_path", "raw_snippet"),
    [
        ("pages/Upload.tsx", "e.message : 'Upload failed.'"),
        ("pages/Upload.tsx", "file\n                {result.queued.length"),
        ("pages/AdvancedSearch.tsx", "{total} result{total !== 1"),
        ("pages/NativeReader.tsx", "alt={`Page ${page + 1}`}"),
        ("pages/BookDetail.tsx", "{' · Book '}"),
        ("pages/Catalog.tsx", "t(choice === 'comfortable'"),
    ],
)
def test_known_residual_raw_spa_copy_does_not_return(relative_path, raw_snippet):
    """Source-pin the raw-copy shapes found by the #886 adversarial sweep.

    The general anchor extractor covers the replacement ``t()`` calls; these
    pins cover the syntactic forms that previously escaped that extractor.
    """
    path = os.path.join(extractor.FRONTEND_SRC, relative_path)
    with open(path, encoding="utf-8") as source_file:
        assert raw_snippet not in source_file.read()


@pytest.mark.parametrize(
    "msgid",
    [
        "Hot — Most Downloaded",
        "Discover — Random Picks",
        "Comfortable",
        "Compact",
        "Dense",
        "{count} files queued for import",
        "{count} results",
        "Page {number}",
        "Book {number}",
        "Currently Reading",
        "Standard (username / password)",
        "Simple (service account)",
    ],
)
def test_dynamic_and_interpolated_residual_keys_are_anchored(msgid):
    assert msgid in extractor.parse_anchored()


def test_magic_shelf_operator_labels_are_translated_at_render_time():
    """The labels are anchored data, but rendering them raw still leaks English."""
    path = os.path.join(extractor.FRONTEND_SRC, "pages", "MagicShelf.tsx")
    with open(path, encoding="utf-8") as source_file:
        source = source_file.read()
    assert "{t(o.label)}" in source
    assert "{o.label}" not in source


def test_catalog_back_link_translates_labels_not_route_segments():
    """Route identifiers (`authors`) are not gettext msgids; the visible back
    link must use static plural labels (`Authors`) that the extractor can see.
    """
    path = os.path.join(extractor.FRONTEND_SRC, "pages", "Catalog.tsx")
    with open(path, encoding="utf-8") as source_file:
        source = source_file.read()
    assert "items: t(ENTITY_PLURAL[entityKind!])" not in source
    assert "items: t(KIND_PLURAL_OPTIONS[entityKind!].label)" in source
    keys = extractor.extract_frontend_keys()
    for label in ("Authors", "Series", "Tags", "Publishers", "Languages", "Ratings", "Formats"):
        assert label in keys


def test_browse_list_lowercases_with_the_app_locale():
    path = os.path.join(extractor.FRONTEND_SRC, "pages", "BrowseList.tsx")
    with open(path, encoding="utf-8") as source_file:
        source = source_file.read()
    assert "const { t, locale } = useI18n()" in source
    assert "toLocaleLowerCase((locale || 'en').replace('_', '-'))" in source
    assert "t(title).toLocaleLowerCase()" not in source


def test_locale_change_invalidates_translated_magic_shelf_names():
    queries = os.path.join(extractor.FRONTEND_SRC, "lib", "queries.ts")
    with open(queries, encoding="utf-8") as source_file:
        source = source_file.read()
    profile_success = source[source.index("export function useUpdateProfile"):
                             source.index("export function useChangePassword")]
    assert "invalidateQueries({ queryKey: ['magicshelves'] })" in profile_success
    assert "invalidateQueries({ queryKey: ['magicshelf'] })" in profile_success


def test_no_spa_anchored_msgid_is_fuzzy_in_any_locale():
    """#879 all-locale quality gate: fuzzy guesses are unsafe to compile and
    silently absent from the SPA, so the update pipeline must reject them.
    """
    result = subprocess.run(
        [sys.executable, _FUZZY_SCRIPT],
        cwd=_REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_fuzzy_migration_clears_guess_instead_of_compiling_it(tmp_path):
    spec = importlib.util.spec_from_file_location("check_spa_fuzzy", _FUZZY_SCRIPT)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    po = tmp_path / "messages.po"
    po.write_text(
        '#, fuzzy, python-brace-format\n'
        '#| msgid "Wrong longer label"\n'
        'msgid "Read {title}"\n'
        'msgstr "Semantically wrong {title}"\n',
        encoding="utf-8",
    )
    assert checker.process(po, {"Read {title}"}, clear=False) == ["Read {title}"]
    assert "fuzzy" in po.read_text(encoding="utf-8")
    assert checker.process(po, {"Read {title}"}, clear=True) == ["Read {title}"]
    migrated = po.read_text(encoding="utf-8")
    assert "fuzzy" not in migrated
    assert "#, python-brace-format" in migrated
    assert 'msgstr ""' in migrated
    assert "Semantically wrong" not in migrated


def test_fuzzy_word_in_translator_comment_cannot_erase_reviewed_translation(tmp_path):
    spec = importlib.util.spec_from_file_location("check_spa_fuzzy_comment", _FUZZY_SCRIPT)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    po = tmp_path / "messages.po"
    original = (
        '# Translator note: reviewed, not fuzzy\n'
        'msgid "Loading"\n'
        'msgstr "Chargement"\n'
    )
    po.write_text(original, encoding="utf-8")
    assert checker.process(po, {"Loading"}, clear=False) == []
    assert checker.process(po, {"Loading"}, clear=True) == []
    assert po.read_text(encoding="utf-8") == original
