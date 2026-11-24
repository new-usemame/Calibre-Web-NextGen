# SPDX-License-Identifier: GPL-3.0-or-later
"""Deleting a book only deletes custom values that no surviving book uses."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_whole_book_delete_preserves_referenced_custom_values():
    # Calibre configures custom columns on the process-global Books mapper.
    # Keep this generated library's column definitions out of the other tests.
    result = subprocess.run(
        [sys.executable, "-m", "tests.unit.test_delete_shared_custom_columns"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def exercise_whole_book_delete():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from cps import db, editbooks, ub

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    columns = [
        db.CustomColumns(id=1, label="topic", datatype="text", is_multiple=False),
        db.CustomColumns(id=2, label="genre", datatype="enumeration", is_multiple=False),
        db.CustomColumns(id=3, label="pages", datatype="int", is_multiple=False),
    ]
    db.CalibreDB.setup_db_cc_classes(columns)
    db.Base.metadata.create_all(engine)
    ub.BookOriginalFilename.__table__.create(engine)

    with Session(engine) as session:
        topic = db.cc_classes[1](value="History")
        genre = db.cc_classes[2](value="Nonfiction")
        session.execute(db.Books.__table__.insert(), [
            {"id": 1, "title": "First"},
            {"id": 2, "title": "Second"},
        ])
        first, second = session.query(db.Books).order_by(db.Books.id).all()
        for book, pages in ((first, 100), (second, 200)):
            book.custom_column_1.append(topic)
            book.custom_column_2.append(genre)
            book.custom_column_3.append(db.cc_classes[3](value=pages))
        session.add_all([*columns, first, second])
        session.commit()

        with (
            patch.object(editbooks, "calibre_db", SimpleNamespace(session=session)),
            patch.object(editbooks.kobo_sync_status, "record_book_deletion"),
            patch.object(editbooks.user_book_data, "purge_user_book_data"),
            patch.object(ub, "session", session),
            patch.object(ub, "session_commit", session.commit),
        ):
            editbooks.delete_whole_book(1, first)
            session.commit()

            assert session.get(db.Books, 1) is None
            assert [row.value for row in second.custom_column_1] == ["History"]
            assert [row.value for row in second.custom_column_2] == ["Nonfiction"]
            assert [row.value for row in session.query(db.cc_classes[3])] == [200]

            editbooks.delete_whole_book(2, second)
            session.commit()
            for model in db.cc_classes.values():
                assert session.query(model).count() == 0

    engine.dispose()


if __name__ == "__main__":
    exercise_whole_book_delete()
