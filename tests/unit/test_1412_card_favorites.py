"""List-card favorite state follows the caller, with one bounded page query."""
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.api import books


@pytest.mark.unit
def test_list_card_favorites_are_page_bounded_and_account_isolated(monkeypatch):
    engine = create_engine('sqlite://')
    ub.FavoriteBook.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        ub.FavoriteBook(user_id=11, book_id=1),
        ub.FavoriteBook(user_id=22, book_id=2),
        ub.FavoriteBook(user_id=11, book_id=99),
    ])
    session.commit()
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(books.config, 'config_read_column', 0, raising=False)
    monkeypatch.setattr(books, 'read_statuses_for_books', lambda *args, **kwargs: {})
    monkeypatch.setattr(books.user_cover, 'overrides_for_user', lambda *args: {})
    monkeypatch.setattr(books, '_visible_shelves_by_book', lambda *args: {})
    entries = [SimpleNamespace(id=n, title=str(n), series_index=1, authors=[],
                               series=[], data=[], has_cover=False) for n in (1, 2)]
    queries = []
    event.listen(engine, 'before_cursor_execute', lambda *args: queries.append(args[2]))
    app = Flask(__name__)
    try:
        with app.test_request_context('/'):
            monkeypatch.setattr(books, '_real_user_id', lambda: 11)
            first = books._rows_to_items(entries)
            assert [item['favorited'] for item in first] == [True, False]
            assert len(queries) == 1
            assert 'book_id IN' in queries[0]
            monkeypatch.setattr(books, '_real_user_id', lambda: 22)
            second = books._rows_to_items(entries)
            assert [item['favorited'] for item in second] == [False, True]
            before = len(queries)
            monkeypatch.setattr(books, '_real_user_id', lambda: None)
            anonymous = books._rows_to_items(entries)
            assert not any(item['favorited'] for item in anonymous)
            assert len(queries) == before
            monkeypatch.setattr(books, '_real_user_id', lambda: 11)
            ub.FavoriteBook.__table__.drop(engine)
            unavailable = books._rows_to_items(entries)
            assert all(item['favorited'] is None for item in unavailable)
    finally:
        session.close()
        engine.dispose()
