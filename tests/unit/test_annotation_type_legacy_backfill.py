# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Proof boundary for deriving legacy ``annotation_type`` values.

The migration may reproduce a value only when the legacy row retains the exact
branch discriminator used by today's writer. Similar-looking content is not a
substitute for that discriminator: NULL is the honest result when provenance is
ambiguous.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("position_type", "expected"),
    (
        ("unanchored", "note"),
        ("cfi", "highlight"),
        # Legacy KoboSpan web-reader rows retain the resulting selector, but
        # not an explicit position_type branch discriminator.
        (None, None),
        ("koreader_xpointer", None),
        ("pdf_quad", None),
        ("comic_page", None),
    ),
)
def test_legacy_type_is_derived_only_from_a_recorded_webreader_branch(
    position_type, expected,
):
    from cps.services.annotation_types import derive_legacy_annotation_type

    assert derive_legacy_annotation_type(position_type=position_type) == expected


@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    from cps import ub
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)

    engine = create_engine(
        "sqlite:///{}".format(tmp_path / "legacy-annotation-types.sqlite"),
        future=True,
    )
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    rows = (
        ub.Annotation(
            # Portable import can rewrite source on an existing row. That does
            # not erase the unanchored branch recorded in position_type.
            user_id=7, book_id=1, annotation_id="web-note", source="koreader",
            position_type="unanchored", note_text="standalone thought",
            annotation_type=None,
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="web-cfi", source="webreader",
            position_type="cfi", cfi_range="epubcfi(/6/2!/4/2:0)",
            annotation_type=None,
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="web-kobospan", source="webreader",
            position_type=None, start_container_path="span#kobo.4.1",
            highlighted_text="anchored, but writer is not provable", annotation_type=None,
        ),
        ub.Annotation(
            # As above, a rewritten source does not invalidate the cfi branch
            # token: no other writer can create that position_type.
            user_id=7, book_id=1, annotation_id="kobo-highlight", source="kobo",
            position_type="cfi", highlighted_text="looks like a highlight",
            annotation_type=None,
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="kobo-dogear", source="kobo",
            position_type=None, highlighted_text="", annotation_type=None,
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="koreader", source="koreader",
            position_type="koreader_xpointer", start_xpointer="/body/DocFragment[1]",
            annotation_type=None,
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="known", source="webreader",
            position_type="unanchored", annotation_type="dogear",
        ),
        ub.Annotation(
            user_id=7, book_id=1, annotation_id="future", source="webreader",
            position_type="cfi", annotation_type="markup",
        ),
    )
    session.add_all(rows)
    session.commit()
    session.close()
    yield engine
    engine.dispose()
    annotation_backup.reset_for_tests()


def _stored_types(engine):
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT annotation_id, annotation_type FROM annotation ORDER BY annotation_id"
        )).all()
        return {annotation_id: annotation_type for annotation_id, annotation_type in rows}


def test_backfill_reports_the_complete_plan_before_writing_and_preserves_unknowns(
    legacy_engine,
):
    from cps import ub

    types_when_reported = []

    def observe_report(message, *args, **_kwargs):
        rendered = message % args if args else message
        if "[annotation-type-backfill] scan" in rendered:
            types_when_reported.append(_stored_types(legacy_engine))

    with patch.object(ub.log, "info", side_effect=observe_report):
        counts = ub.backfill_legacy_annotation_types(legacy_engine)

    assert counts == {
        "total": 8,
        "already_typed": 2,
        "null": 6,
        "derived_note": 1,
        "derived_highlight": 2,
        "unknown": 3,
        "would_update": 3,
        "updated": 3,
    }
    assert len(types_when_reported) == 1
    assert types_when_reported[0]["web-note"] is None
    assert types_when_reported[0]["web-cfi"] is None
    assert types_when_reported[0]["kobo-highlight"] is None

    after = _stored_types(legacy_engine)
    assert after["web-note"] == "note"
    assert after["web-cfi"] == "highlight"
    assert after["web-kobospan"] is None
    assert after["kobo-highlight"] == "highlight"
    assert after["kobo-dogear"] is None
    assert after["koreader"] is None
    assert after["known"] == "dogear"
    assert after["future"] == "markup"


def test_backfill_is_idempotent_and_the_second_scan_reports_no_candidates(
    legacy_engine,
):
    from cps import ub

    first = ub.backfill_legacy_annotation_types(legacy_engine)
    snapshot = _stored_types(legacy_engine)
    second = ub.backfill_legacy_annotation_types(legacy_engine)

    assert first["updated"] == 3
    assert second == {
        "total": 8,
        "already_typed": 5,
        "null": 3,
        "derived_note": 0,
        "derived_highlight": 0,
        "unknown": 3,
        "would_update": 0,
        "updated": 0,
    }
    assert _stored_types(legacy_engine) == snapshot


def test_value_written_after_scan_wins_over_the_legacy_derivation(legacy_engine):
    from cps import ub

    wrote_concurrent_value = False

    def write_after_report(message, *_args, **_kwargs):
        nonlocal wrote_concurrent_value
        if "[annotation-type-backfill] scan" not in message or wrote_concurrent_value:
            return
        # The scan has completed and been reported, but the migration's write
        # transaction has not started. Model a live writer winning that race.
        with legacy_engine.begin() as connection:
            connection.execute(text(
                "UPDATE annotation SET annotation_type='markup' "
                "WHERE annotation_id='web-note'"
            ))
        wrote_concurrent_value = True

    with patch.object(ub.log, "info", side_effect=write_after_report):
        counts = ub.backfill_legacy_annotation_types(legacy_engine)

    assert wrote_concurrent_value is True
    assert counts["would_update"] == 3
    assert counts["updated"] == 2
    assert _stored_types(legacy_engine)["web-note"] == "markup"


def test_stage0_startup_migration_runs_the_legacy_type_backfill(legacy_engine):
    from cps import ub

    ub.migrate_kobo_two_way_annotation_sync(legacy_engine, None)

    after = _stored_types(legacy_engine)
    assert after["web-note"] == "note"
    assert after["web-cfi"] == "highlight"
    assert after["web-kobospan"] is None
    assert after["kobo-highlight"] == "highlight"
