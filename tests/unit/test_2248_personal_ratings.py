"""Personal scores, explicit household consent and paginated attached SQL."""
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import scoped_session, sessionmaker

from cps import config, constants, db, ub


@pytest.fixture
def ratings_db(monkeypatch):
    monkeypatch.setattr(config, 'config_read_column', 0, raising=False)
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute(
        "ATTACH DATABASE ':memory:' AS calibre"))
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    users = [ub.User(name="reader%d" % n, email="r%d@example.invalid" % n,
                     password="", role=constants.ROLE_USER) for n in range(3)]
    session.add_all(users)
    for n in range(1, 5):
        book = db.Books("Book%d" % n, "Book%d" % n, "Author", stamp,
                        stamp, "1.0", stamp, "Author/Book%d" % n, 1, [], [])
        book.id = n
        book.uuid = "rating-book-%d" % n
        session.add(book)
    session.commit()
    yield session, users, stamp, engine, monkeypatch
    session.close()
    engine.dispose()


@pytest.mark.unit
def test_personal_sql_orders_and_filters_before_limit_without_shared_fallback(ratings_db):
    from cps.personal_ratings import personal_score
    from cps.sort_orders import book_sort_order

    session, users, stamp, engine, monkeypatch = ratings_db
    session.add_all([
        ub.BookRating(user_id=users[0].id, book_id=1, rating=6),
        ub.BookRating(user_id=users[0].id, book_id=3, rating=10),
        ub.BookRating(user_id=users[1].id, book_id=2, rating=10),
    ])
    shared = db.Ratings(rating=10)
    session.add(shared)
    session.query(db.Books).filter_by(id=4).one().ratings.append(shared)
    session.commit()
    score = personal_score(users[0].id)
    query = session.query(db.Books.id).order_by(*book_sort_order("ratingdesc", users[0].id))
    assert [r[0] for r in query.limit(2)] == [3, 1]
    assert [r[0] for r in query.offset(2).limit(2)] == [4, 2]
    assert [r[0] for r in session.query(db.Books.id).order_by(
        *book_sort_order('ratingasc', users[0].id))] == [1, 3, 4, 2]
    assert [r[0] for r in session.query(db.Books.id).filter(score == 10)] == [3]
    assert {r[0] for r in session.query(db.Books.id).filter(score == 0)} == {2, 4}
    assert session.query(db.Books).filter_by(id=4).one().ratings[0].rating == 10
    monkeypatch.setattr(db, 'current_user', users[0])
    monkeypatch.setattr(db.CalibreDB, 'engine', engine)
    monkeypatch.setattr(db.CalibreDB, 'session_factory', scoped_session(sessionmaker(bind=engine)))
    monkeypatch.setattr(db.CalibreDB, 'config', SimpleNamespace(
        config_books_per_page=60, config_random_books=4,
        config_restricted_column=0, config_read_column=0))
    app = flask.Flask(__name__)
    with app.test_request_context('/api/v1/books'):
        library = db.CalibreDB()
        for page, expected in [(1, [3, 1]), (2, [4, 2])]:
            entries, _, pagination = library.fill_indexpage(
                page, 2, db.Books, True, book_sort_order('ratingdesc', users[0].id),
                True, 0, db.books_series_link,
                db.Books.id == db.books_series_link.c.book, db.Series)
            assert [entry.Books.id for entry in entries] == expected
            assert pagination.total_count == 4


@pytest.mark.unit
def test_household_average_requires_actual_boolean_consent_and_never_names(ratings_db):
    from cps.personal_ratings import ratings_for_books

    session, users, stamp, engine, monkeypatch = ratings_db
    session.add_all([ub.BookRating(user_id=u.id, book_id=1, rating=r)
                     for u, r in zip(users, (2, 8, 10))])
    users[1].view_settings = {"preferences": {"share_book_ratings": True}}
    users[2].view_settings = {"preferences": {"share_book_ratings": "true"}}
    guest = ub.User(name='guest-rating', email='guest-rating@example.invalid',
                    password='', role=constants.ROLE_ANONYMOUS,
                    view_settings={'preferences': {'share_book_ratings': True}})
    session.add(guest)
    session.flush()
    session.add(ub.BookRating(user_id=guest.id, book_id=1, rating=10))
    session.commit()
    item = ratings_for_books(session, users[0].id, [1, 2])[1]
    assert item == {"personal_rating": 2, "household_rating": 8.0}
    assert ratings_for_books(session, None, [1])[1] == {
        "personal_rating": None, "household_rating": None}
    users[0].view_settings = {"preferences": {"share_book_ratings": True}}
    session.commit()
    assert ratings_for_books(session, users[0].id, [1])[1]["household_rating"] == 5
    # Malformed legacy JSON in someone else's row must fail closed, not break
    # all readers' detail pages or treat strings/numbers as permission.
    session.execute(text("UPDATE user SET view_settings='broken json' WHERE id=:id"),
                    {"id": users[2].id})
    session.commit()
    assert ratings_for_books(session, users[0].id, [1])[1]["household_rating"] == 5
    users[1].view_settings = {"preferences": {"share_book_ratings": False}}
    session.commit()
    assert ratings_for_books(session, users[0].id, [1])[1]["household_rating"] == 2
    # Bare-metal SQLite without JSON1 must still load private scores and
    # honor exactly the same opt-in/opt-out household rules.
    monkeypatch.setattr(db, '_SQLITE_JSON_CAPABILITY', False)
    def unavailable(_value):
        raise RuntimeError('JSON functions absent on this supported installation')
    raw = engine.raw_connection()
    raw.create_function('json_valid', 1, unavailable)
    raw.create_function('json_type', 2, lambda *_args: unavailable(None))
    raw.close()
    assert ratings_for_books(session, users[0].id, [1])[1] == {
        'personal_rating': 2, 'household_rating': 2.0}
    users[0].view_settings = {'preferences': {'share_book_ratings': False}}
    session.commit()
    assert ratings_for_books(session, users[0].id, [1])[1] == {
        'personal_rating': 2, 'household_rating': None}


@pytest.mark.unit
def test_duplicate_merge_keeps_latest_clear_and_purges_each_owner(ratings_db):
    from cps.user_book_data import migrate_user_book_data, purge_user_book_data

    session, users, stamp, engine, monkeypatch = ratings_db
    session.add_all([
        ub.BookRating(user_id=users[0].id, book_id=1, rating=8, updated_at=stamp),
        ub.BookRating(user_id=users[0].id, book_id=2, rating=0,
                      updated_at=stamp + timedelta(days=1)),
        ub.BookRating(user_id=users[1].id, book_id=1, rating=6, updated_at=stamp),
    ])
    session.commit()
    migrate_user_book_data(2, 1, session=session)
    session.commit()
    assert session.query(ub.BookRating).filter_by(user_id=users[0].id, book_id=1).one().rating == 0
    assert session.query(ub.BookRating).filter_by(book_id=2).count() == 0
    purge_user_book_data(user_id=users[0].id, session=session)
    session.commit()
    assert session.query(ub.BookRating).one().user_id == users[1].id
    purge_user_book_data(book_id=1, session=session)
    session.commit()
    assert session.query(ub.BookRating).count() == 0


@pytest.mark.unit
def test_rating_http_owns_account_validates_half_stars_and_preserves_calibre(ratings_db, monkeypatch):
    from cps.api import book_ratings

    session, users, stamp, engine, monkeypatch = ratings_db
    shared = db.Ratings(rating=10)
    book = session.query(db.Books).filter_by(id=1).one()
    book.ratings.append(shared)
    session.commit()
    monkeypatch.setattr(book_ratings, 'current_user', users[0])
    visibility_calls = []
    def visible(book_id, read_column, **kwargs):
        visibility_calls.append(kwargs)
        return book if book_id == 1 else None
    monkeypatch.setattr(book_ratings, 'calibre_db', SimpleNamespace(get_book_read_archived=visible))
    app = flask.Flask(__name__)
    app.add_url_rule('/books/<int:book_id>/rating', view_func=inspect.unwrap(book_ratings.book_rating),
                     methods=['GET', 'PUT', 'DELETE'])
    client = app.test_client()
    url = '/books/1/rating'
    response = client.get(url)
    assert response.json == {'personal_rating': None, 'household_rating': None}
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    for value in (True, 5.5, '10', None, -1, 11):
        assert client.put(url, json={'rating': value}).status_code == 400
    assert session.query(ub.BookRating).count() == 0
    assert client.put(url, json={'rating': 9, 'user_id': users[1].id}).json == {'personal_rating': 9}
    assert session.query(ub.BookRating).one().user_id == users[0].id
    monkeypatch.setattr(book_ratings, 'current_user', users[1])
    assert client.get(url).json['personal_rating'] is None
    assert client.put(url, json={'rating': 4}).status_code == 200
    monkeypatch.setattr(book_ratings, 'current_user', users[0])
    assert client.delete(url).json == {'personal_rating': None}
    assert client.delete(url).status_code == 200
    assert session.query(ub.BookRating).filter_by(user_id=users[0].id).one().rating == 0
    assert session.query(ub.BookRating).filter_by(user_id=users[1].id).one().rating == 4
    session.expire_all()
    assert book.ratings[0].rating == 10
    assert visibility_calls[-1]['allow_public_shelf_books'] is True
    assert client.put('/books/2/rating', json={'rating': 8}).status_code == 404
    users[0].role = constants.ROLE_ANONYMOUS
    for method in ('get', 'put', 'delete'):
        assert getattr(client, method)(url, json={'rating': 8}).status_code == 401


@pytest.mark.unit
def test_failed_rating_write_rolls_back_and_leaves_previous_score(ratings_db, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from cps.api import book_ratings

    session, users, stamp, engine, monkeypatch = ratings_db
    session.add(ub.BookRating(user_id=users[0].id, book_id=1, rating=8))
    session.commit()
    monkeypatch.setattr(book_ratings, 'current_user', users[0])
    monkeypatch.setattr(book_ratings, 'calibre_db', SimpleNamespace(get_book_read_archived=lambda *a, **kw: object()))
    monkeypatch.setattr(session, 'commit', lambda: (_ for _ in ()).throw(OperationalError('commit', {}, Exception('busy'))))
    app = flask.Flask(__name__)
    app.add_url_rule('/books/<int:book_id>/rating', view_func=inspect.unwrap(book_ratings.book_rating), methods=['PUT'])
    response = app.test_client().put('/books/1/rating', json={'rating': 2})
    assert response.status_code == 500
    assert response.json['error']['code'] == 'rating_save_failed'
    assert session.query(ub.BookRating).one().rating == 8


@pytest.mark.unit
def test_classic_library_score_is_labelled_fractional_and_account_hideable():
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader

    templates = Path(__file__).resolve().parents[2] / 'cps/templates'
    fragment = Environment(loader=FileSystemLoader(templates)).get_template('library_rating.html')
    def render(score, visible):
        return fragment.render(library_rating=score, _=lambda value: value,
            current_user=SimpleNamespace(get_view_property=lambda *_args: visible))
    assert 'Library rating: 4.5/5' in render(9, None)
    assert 'Library rating' not in render(9, False)
    assert 'Library rating' not in render(0, True)
