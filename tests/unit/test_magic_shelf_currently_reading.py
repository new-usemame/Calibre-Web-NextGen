# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests pinning the "Currently Reading" magic-shelf preset
added in fork PR #233 (backport of upstream CWA #1201 by @Sheol27).

The fix introduces a third built-in read_status value (2 =
STATUS_IN_PROGRESS) so users can build a magic shelf that surfaces
books KOSync/Kobo has marked in-progress. Before the fix, the
QueryBuilder treated read_status as a boolean (0/1 only), and
build_filter_from_rule only resolved the unread/finished branches —
in-progress would silently fall through to "finished" matching.

Three invariants this test pins:

1. SYSTEM_SHELF_TEMPLATES has a 'currently_reading' entry whose only
   rule matches read_status == STATUS_IN_PROGRESS (value 2). If a
   future edit removes the template or changes the value to 0/1, the
   preset stops surfacing in-progress books and the test fires.

2. The 'yet_to_read' preset's icon was swapped from 📖 to 📚 so it
   doesn't collide with the new currently_reading 📖. If someone
   reverts that without thinking, both presets render with the same
   icon and the UI becomes ambiguous.

3. build_filter_from_rule's read_status fallback (the no-custom-column
   path) handles all three statuses — STATUS_IN_PROGRESS, STATUS_FINISHED,
   STATUS_UNREAD — and the STATUS_IN_PROGRESS branch issues a query
   filtered on STATUS_IN_PROGRESS, not STATUS_FINISHED. Source-pinned
   because the function reaches into ub.session at runtime and is
   awkward to invoke from a unit test without full app init.
"""

from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest


@pytest.mark.unit
class TestCurrentlyReadingTemplate:
    def test_template_exists_with_in_progress_rule(self):
        from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

        assert 'currently_reading' in SYSTEM_SHELF_TEMPLATES, (
            "SYSTEM_SHELF_TEMPLATES must expose a 'currently_reading' "
            "preset so users can build a magic shelf that surfaces "
            "in-progress (KOSync/Kobo-synced) books."
        )

        tmpl = SYSTEM_SHELF_TEMPLATES['currently_reading']
        assert tmpl['name'] == 'Currently Reading'
        rules = tmpl['rules']['rules']
        assert len(rules) == 1, (
            "currently_reading should be a single-rule preset — "
            "read_status == STATUS_IN_PROGRESS — not a compound filter."
        )
        rule = rules[0]
        assert rule['id'] == 'read_status'
        assert rule['field'] == 'read_status'
        assert rule['operator'] == 'equal'
        assert rule['value'] == 2, (
            "currently_reading must match value 2 (STATUS_IN_PROGRESS). "
            "Values 0/1 would re-bind it to unread/finished and the "
            "preset would silently match the wrong books."
        )

    def test_in_progress_value_matches_ub_constant(self):
        """Cross-pin: the literal 2 in the template must equal
        ub.ReadBook.STATUS_IN_PROGRESS. If someone renumbers the
        ReadBook status constants, this test catches the silent drift
        before the preset starts matching the wrong rows."""
        from cps import ub
        from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

        rule = SYSTEM_SHELF_TEMPLATES['currently_reading']['rules']['rules'][0]
        assert rule['value'] == ub.ReadBook.STATUS_IN_PROGRESS

    def test_yet_to_read_icon_avoids_collision(self):
        """yet_to_read was 📖 in plain calibre-web; the backport
        swapped it to 📚 so the new currently_reading preset could
        own 📖. If a future edit reverts this without re-checking
        currently_reading, both presets render with the same emoji."""
        from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

        yet_icon = SYSTEM_SHELF_TEMPLATES['yet_to_read']['icon']
        cur_icon = SYSTEM_SHELF_TEMPLATES['currently_reading']['icon']
        assert yet_icon != cur_icon, (
            "yet_to_read and currently_reading must use distinct icons "
            "so the magic-shelf picker is unambiguous. yet_to_read "
            "should be 📚; currently_reading should be 📖."
        )
        assert yet_icon == '📚'
        assert cur_icon == '📖'


@pytest.mark.unit
def test_legacy_magic_read_rules_select_real_statuses_and_include_untouched_books(monkeypatch):
    """The actual SQL must distinguish in-progress from read and retain absent rows."""
    from cps import db, ub, config, magic_shelf
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    ub.ReadBook.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    for book_id in range(1, 5):
        session.execute(db.Books.__table__.insert().values(
            id=book_id, title=str(book_id), sort=str(book_id), author_sort="A",
            uuid=str(book_id), series_index=1, path="A/" + str(book_id), has_cover=0))
    for book_id, status in ((1, 0), (2, 1), (3, 2)):
        session.add(ub.ReadBook(user_id=7, book_id=book_id, read_status=status))
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)
    for value, expected in (("0", [1, 3, 4]), ("1", [2]), ("2", [3])):
        condition = magic_shelf.build_filter_from_rule(
            {"id": "read_status", "operator": "equal", "value": value}, user_id=7)
        assert sorted(book_id for book_id, in session.query(db.Books.id).filter(condition)) == expected
    session.close()
    engine.dispose()


@pytest.mark.unit
class TestQueryBuilderTemplateExposesThreeRadioValues:
    """The Jinja template for magic-shelf edit ships the QueryBuilder
    field definition for read_status. It must declare the type as
    'integer' (not 'boolean') and expose all three radio values
    (0/1/2). If a future edit reverts to boolean or drops the
    "Currently Reading" radio, users can no longer create the preset
    through the UI even though the Python side still supports it."""

    def test_template_declares_integer_radio_with_three_values(self):
        from cps import magic_shelf

        fields = {field["id"]: field for field in magic_shelf.build_rule_schema()["fields"]}
        read_status = fields["read_status"]
        assert read_status["type"] == "integer", (
            "The QueryBuilder read_status field must declare "
            "type: 'integer' so all three values (0=Unread, "
            "1=Read, 2=Currently Reading) are valid. 'boolean' "
            "collapses to 0/1 only."
        )
        assert read_status["input"] == "radio"
        assert read_status["values"] == {0: "Unread", 2: "Currently Reading", 1: "Read",
                                        3: "Did not finish", 4: "On hold"}
