# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""F-7e418c, end of the path: the STORED row must carry the device's type.

Reading `Bookmark.Type` in the parser is only half the fix. If `ingest_bookmarks`
does not pass it into the `ub.Annotation` it builds, the column stays NULL and
nothing downstream can tell a recovered highlight from a recovered anything-else.

That half was genuinely uncovered: deleting the `annotation_type=` argument from
the row construction left 394 tests green. This file is the test that fails.

Why the column matters: `cps/services/annotation_sync/__init__.py` stores
`payload["type"]` for an annotation arriving over the wire, so without this the
same highlight has a type when it syncs live and NULL when it is recovered from
the device's own sqlite — two writers to one column, disagreeing, which is the
shape that produced the highlight_color mess `annotation_colors.py` exists to
undo.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.fixtures.kobo_reader_sqlite import (
    build_kobo_db_with_bookmark_type,
    build_kobo_db_without_bookmark_type,
)

pytestmark = pytest.mark.unit

BOOK_ID = 42
BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"


@pytest.fixture
def memory_db(tmp_path, monkeypatch):
    from cps import ub, constants
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    yield session
    session.close()
    annotation_backup.reset_for_tests()


def _book_lookup(uuid):
    return SimpleNamespace(id=BOOK_ID) if uuid == BOOK_UUID else None


def _import(session, db_path):
    from cps import ub
    from cps.annotations import ingest_bookmarks

    ingest_bookmarks(db_path, user_id=7, session=session,
                     book_lookup=_book_lookup, commit=session.commit)
    return {r.annotation_id: r for r in
            session.query(ub.Annotation).filter_by(user_id=7).all()}


def test_the_stored_row_carries_the_device_word(memory_db, tmp_path):
    rows = _import(memory_db, build_kobo_db_with_bookmark_type(tmp_path / "k.sqlite"))
    assert rows, "nothing was imported; the assertions below would be vacuous"
    assert rows["bt-001"].annotation_type == "highlight"
    assert rows["bt-002"].annotation_type == "highlight"


def test_a_non_highlight_word_survives_into_storage(memory_db, tmp_path):
    """Preserve, don't classify — the same rule the colour table follows."""
    rows = _import(memory_db, build_kobo_db_with_bookmark_type(tmp_path / "k.sqlite"))
    assert rows["bt-003"].annotation_type == "dogear"


def test_an_empty_device_word_is_stored_as_null(memory_db, tmp_path):
    rows = _import(memory_db, build_kobo_db_with_bookmark_type(tmp_path / "k.sqlite"))
    assert rows["bt-004"].annotation_type is None


def test_an_older_schema_still_imports_and_stores_null(memory_db, tmp_path):
    """No Type column on the device is not an error, and must not lose rows."""
    rows = _import(
        memory_db,
        build_kobo_db_without_bookmark_type(tmp_path / "old.sqlite"),
    )
    assert len(rows) >= 3, "the older-schema import lost rows"
    assert {r.annotation_type for r in rows.values()} == {None}


def test_the_two_schemas_produce_different_stored_types(memory_db, tmp_path):
    """Vacuity guard.

    Each assertion above would also hold if `annotation_type` were always None
    and the fixture happened to agree. Pin that the typed schema and the untyped
    one actually differ once stored.
    """
    typed = _import(memory_db, build_kobo_db_with_bookmark_type(tmp_path / "k.sqlite"))
    typed_values = {r.annotation_type for r in typed.values()}
    assert typed_values != {None}, typed_values
    assert "highlight" in typed_values
