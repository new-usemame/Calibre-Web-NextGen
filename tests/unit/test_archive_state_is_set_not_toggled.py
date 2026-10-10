# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Archive and unarchive of a selection set the state; only a bare toggle flips it.

"Unarchive selected" in the books table, and the table's archive column, send
an explicit False. Before, False fell through to "toggle", so unarchiving a
selection archived every selected book that was not archived.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import kobo_sync_status, ub


pytestmark = pytest.mark.unit

USER = SimpleNamespace(id=1)
NEVER_ARCHIVED, UNARCHIVED, ARCHIVED = 10, 11, 12


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(ub.ArchivedBook(user_id=USER.id, book_id=UNARCHIVED, is_archived=False))
    s.add(ub.ArchivedBook(user_id=USER.id, book_id=ARCHIVED, is_archived=True))
    s.commit()
    yield s
    s.close()


def _archive(session, book_id, state):
    with patch.object(kobo_sync_status, "current_user", USER):
        kobo_sync_status.change_archived_books(book_id, state, session=session)
    row = session.query(ub.ArchivedBook).filter_by(user_id=USER.id, book_id=book_id).first()
    return bool(row and row.is_archived)


@pytest.mark.parametrize("state", [False, True], ids=["unarchive", "archive"])
def test_a_selection_ends_in_the_requested_state_whatever_each_book_was(session, state):
    assert [_archive(session, b, state) for b in (NEVER_ARCHIVED, UNARCHIVED, ARCHIVED)] == [state] * 3


def test_a_bare_toggle_still_flips(session):
    assert [_archive(session, b, None) for b in (NEVER_ARCHIVED, UNARCHIVED, ARCHIVED)] == [True, True, False]
