# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced search must retain every matching ID for shelf mass-add (#2169)."""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, literal
from sqlalchemy.orm import sessionmaker

from cps import db, search, ub


pytestmark = pytest.mark.unit


@pytest.fixture
def search_state(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    db.Base.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Core inserts leave the ORM identity map empty so the load listener below
    # detects even temporary materialization of books outside the visible page.
    session.execute(db.Books.__table__.insert(), [
        {"id": i, "title": "Match" if i <= 67 else "Other",
         "sort": f"{i:03}", "path": "", "author_sort": ""}
        for i in range(1, 69)
    ])
    session.commit()
    query = (session.query(db.Books, literal(False).label("is_archived"))
             .filter(db.Books.title == "Match"))
    monkeypatch.setattr(search, "build_adv_search_query", lambda term: (query, "Match"))
    monkeypatch.setattr(search.calibre_db, "session", session)
    monkeypatch.setattr(search.calibre_db, "ensure_session", lambda: None)
    monkeypatch.setattr(search, "render_title_template", lambda template, **ctx: ctx)
    monkeypatch.setattr(search, "_", lambda text: text)
    monkeypatch.setattr(ub, "current_user", SimpleNamespace(id=1))
    monkeypatch.setattr(ub, "searched_ids", {1: [999], 2: [888]})
    loaded = []

    def record_load(book, context):
        loaded.append(book.id)

    event.listen(db.Books, "load", record_load)
    app = Flask(__name__)
    app.secret_key = "test"
    try:
        with app.test_request_context():
            yield SimpleNamespace(session=session, loaded=loaded, app=app)
    finally:
        event.remove(db.Books, "load", record_load)
        session.close()
        engine.dispose()


@pytest.mark.parametrize("offset,limit,empty", [
    pytest.param(0, 60, False, id="first-page"),
    pytest.param(60, 60, False, id="second-page"),
    pytest.param(120, 60, False, id="past-last-page"),
    pytest.param(None, None, False, id="unpaged"),
    pytest.param(None, 60, False, id="no-offset"),
    pytest.param(60, None, False, id="no-limit"),
    pytest.param(0, 60, True, id="empty-page"),
    pytest.param(None, None, True, id="empty-unpaged"),
])
def test_advanced_search_stores_all_matches_without_loading_off_page_books(
    search_state, offset, limit, empty
):
    if empty:
        search_state.session.query(db.Books).delete()
        search_state.session.commit()
    matching_ids = [] if empty else list(range(67, 0, -1))
    paged = offset is not None and limit is not None
    visible_ids = matching_ids[offset:offset + limit] if paged else matching_ids

    rendered = search.render_adv_search_results(
        {"title": "Match"}, offset=offset, limit=limit,
        order=([db.Books.sort.desc()], "sortdesc"),
    )

    assert [row.Books.id for row in rendered["entries"]] == visible_ids
    assert rendered["result_count"] == len(matching_ids)
    pagination = rendered["pagination"]
    assert (pagination.page, pagination.per_page, pagination.total_count) == (
        offset // limit + 1 if paged else 1,
        limit if paged else max(len(matching_ids), 1),
        len(matching_ids),
    )
    assert sorted(search_state.loaded) == sorted(visible_ids)
    assert ub.searched_ids[1] == matching_ids
    assert ub.searched_ids[2] == [888]


@pytest.mark.parametrize("existing_ids", [[], list(range(8, 68))],
                         ids=["empty-shelf", "first-page-already-shelved"])
def test_massadd_persists_all_67_matches_and_repeat_add_is_idempotent(
    search_state, monkeypatch, existing_ids
):
    from cps import shelf

    session = search_state.session
    target = ub.Shelf(name="Search results", is_public=0, user_id=1)
    session.add(target)
    session.flush()
    for position, book_id in enumerate(existing_ids, 1):
        target.books.append(ub.BookShelf(shelf=target.id, book_id=book_id, order=position))
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(shelf, "current_user", SimpleNamespace(id=1))
    monkeypatch.setattr(shelf, "_", lambda text, **kwargs: text)
    monkeypatch.setattr(shelf, "queue_hardcover_sync", lambda *args: None)
    monkeypatch.setattr(shelf.config, "config_allow_reverse_proxy_header_login", False,
                        raising=False)
    app = search_state.app
    app.config.update(TESTING=True, LOGIN_DISABLED=True)
    app.register_blueprint(shelf.shelf)
    app.add_url_rule("/", endpoint="web.index", view_func=lambda: "index")

    rendered = search.render_adv_search_results(
        {"title": "Match"}, offset=0, limit=60,
        order=([db.Books.sort.desc()], "sortdesc"),
    )
    assert len(rendered["entries"]) == 60
    client = app.test_client()
    for _ in range(2):
        response = client.post(f"/shelf/massadd/{target.id}")
        assert response.status_code == 302
        rows = session.query(ub.BookShelf).filter_by(shelf=target.id).all()
        assert sorted(row.book_id for row in rows) == list(range(1, 68))
        assert sorted(row.order for row in rows) == list(range(1, 68))
