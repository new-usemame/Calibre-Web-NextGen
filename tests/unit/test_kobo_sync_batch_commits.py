# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


@pytest.mark.unit
def test_sync_page_uses_one_commit_and_inserts_only_missing_user_book_pairs(monkeypatch):
    """An N-book page is one transaction and remains user-keyed/idempotent."""
    from cps import kobo_sync_status, ub

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        ub.KoboSyncedBooks(user_id=7, book_id=2),
        ub.KoboSyncedBooks(user_id=99, book_id=3),
    ])
    session.commit()

    commits = 0

    def commit_once(*_args, **_kwargs):
        nonlocal commits
        commits += 1
        session.commit()
        return True

    monkeypatch.setattr(kobo_sync_status.ub, "session", session)
    monkeypatch.setattr(kobo_sync_status.ub, "session_commit", commit_once)
    monkeypatch.setattr(kobo_sync_status, "current_user", SimpleNamespace(id=7))

    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_statements(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    # Repeated IDs model defensive page input; book 2 already belongs to this
    # user, while book 3 currently belongs only to another user.
    kobo_sync_status.add_synced_books_batch([1, 2, 2, 3, 4])

    assert commits == 1
    assert statements.count("SELECT") == 1
    assert statements.count("INSERT") == 1
    assert {
        row.book_id
        for row in session.query(ub.KoboSyncedBooks).filter_by(user_id=7).all()
    } == {1, 2, 3, 4}
    assert session.query(ub.KoboSyncedBooks).filter_by(user_id=7, book_id=2).count() == 1
    assert session.query(ub.KoboSyncedBooks).filter_by(user_id=99, book_id=3).count() == 1

    session.close()
    engine.dispose()
