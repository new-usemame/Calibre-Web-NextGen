# SPDX-License-Identifier: GPL-3.0-or-later
"""Editing a tag only walks the links for the book being edited."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from cps import db, editbooks


@pytest.mark.unit
def test_edit_tags_preserves_shared_tag_without_loading_its_books(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
        db.Base.metadata.create_all(connection)
        connection.execute(db.Books.__table__.insert(), [
            {"id": book_id, "title": f"Book {book_id}"} for book_id in range(1, 129)
        ])
        connection.execute(db.Tags.__table__.insert(), [
            {"id": 1, "name": "Shared"}, {"id": 2, "name": "Unused after edit"}
        ])
        connection.execute(db.books_tags_link.insert(), [
            {"book": book_id, "tag": 1} for book_id in range(1, 129)
        ] + [{"book": 1, "tag": 2}])

    try:
        with Session(engine) as session:
            monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(session=session))
            book = session.get(db.Books, 1)
            loaded_books = []

            @event.listens_for(session, "loaded_as_persistent")
            def count_book_loads(_session, instance):
                if isinstance(instance, db.Books):
                    loaded_books.append(instance.id)

            assert editbooks.edit_book_tags("", book)
            assert loaded_books == [], "removing one link loaded the other books"
            session.commit()

            assert book.tags == []
            assert session.get(db.Tags, 1).name == "Shared"
            assert session.get(db.Tags, 2) is None
            assert session.query(db.books_tags_link).count() == 127
    finally:
        engine.dispose()
