# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the Kobo guidance in the delete-book dialog."""

from pathlib import Path
from types import SimpleNamespace

import jinja2
import polib
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "cps" / "templates"
TRANSLATIONS_DIR = REPO_ROOT / "cps" / "translations"

OLD_MSGIDS = (
    "Important Kobo Note: deleted books will remain on any paired Kobo device.",
    "Books must first be archived and the device synced before a book can safely be deleted.",
)
NEW_MSGID = (
    "Deleting this book also tries to archive its copy on paired Kobo devices the next time "
    "they sync. If that update cannot be recorded, the book may remain on a device."
)


def _render_delete_dialog(kobo_sync):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    wrapper = environment.from_string(
        "{% from 'modal_dialogs.html' import delete_book with context %}"
        "{{ delete_book(True) }}"
    )
    return wrapper.render(
        config=SimpleNamespace(config_kobo_sync=kobo_sync),
        _=lambda message: message,
    )


@pytest.mark.unit
def test_rendered_kobo_delete_guidance_is_honest_and_conditional():
    enabled = _render_delete_dialog(True)
    disabled = _render_delete_dialog(False)

    assert NEW_MSGID in enabled
    assert all(stale not in enabled for stale in OLD_MSGIDS)
    assert NEW_MSGID not in disabled
    assert all(stale not in disabled for stale in OLD_MSGIDS)


@pytest.mark.unit
def test_user_facing_sources_have_no_archive_before_delete_guidance():
    roots = [REPO_ROOT / "README.md", REPO_ROOT / "docs", TEMPLATE_DIR]
    stale_sites = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for msgid in OLD_MSGIDS:
                if msgid in source:
                    stale_sites.append(str(path.relative_to(REPO_ROOT)))
    assert stale_sites == []


@pytest.mark.unit
def test_changed_msgid_does_not_inherit_old_translations():
    pot = polib.pofile(str(REPO_ROOT / "messages.pot"))
    assert pot.find(NEW_MSGID, include_obsolete_entries=False) is not None
    assert all(pot.find(old, include_obsolete_entries=False) is None for old in OLD_MSGIDS)

    failures = []
    for po_path in sorted(TRANSLATIONS_DIR.glob("*/LC_MESSAGES/messages.po")):
        catalog = polib.pofile(str(po_path))
        locale = po_path.parents[1].name
        entry = catalog.find(NEW_MSGID, include_obsolete_entries=False)
        obsolete_old_translations = {
            old_entry.msgstr for old_entry in catalog.obsolete_entries()
            if old_entry.msgid in OLD_MSGIDS and old_entry.msgstr
        }
        old_entries = [
            old for old in OLD_MSGIDS
            if catalog.find(old, include_obsolete_entries=False) is not None
        ]
        inherited_translation = (
            entry is not None
            and bool(entry.msgstr)
            and entry.msgstr in obsolete_old_translations
        )
        if entry is None or old_entries or inherited_translation:
            failures.append({
                "locale": locale,
                "new_msgid_present": entry is not None,
                "new_msgstr": None if entry is None else entry.msgstr,
                "active_old_msgids": old_entries,
                "inherited_old_translation": inherited_translation,
            })
    assert failures == []
