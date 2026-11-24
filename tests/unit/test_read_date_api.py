# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-date sorts reach paginated API lists and exported rows, per viewer."""
import csv
import inspect
import io
from datetime import datetime
from urllib.parse import urlencode

import pytest

from cps import ub
from cps.api import books
from tests.unit.test_custom_sort_export_merge import export_library, classic_library, sortable_library

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("sort,expected", [("readnew", [3, 2, 1]), ("readold", [1, 3, 2])])
def test_read_date_list_pagination_and_export_agree(export_library, sort, expected):
    state = export_library
    state.session.query(ub.ReadBook).delete()
    for book, year in [(1, 2023), (2, 2025), (3, 2025)]:
        state.session.add(ub.ReadBook(user_id=7, book_id=book,
            read_status=ub.ReadBook.STATUS_FINISHED, read_finished_at=datetime(year, 1, 1)))
    state.session.add(ub.ReadBook(user_id=8, book_id=5,
        read_status=ub.ReadBook.STATUS_FINISHED, read_finished_at=datetime(2030, 1, 1)))
    state.session.commit()

    found = []
    for page in [1, 2]:
        params = urlencode({"filter": "read", "sort": sort, "per_page": 2, "page": page})
        with state.app.test_request_context("/api/v1/books?" + params):
            result = inspect.unwrap(books.list_books)().get_json()
        assert result["sort"] == sort
        assert result["total"] == 3
        found.extend(item["id"] for item in result["items"])
    assert found == expected

    for source, params in [("catalog", {"filter": "read"}), ("advanced", {"read_status": "read"})]:
        with state.app.test_request_context("/api/v1/books/export", method="POST", json={
                "source": source, "format": "csv", "params": {**params, "sort": sort}}):
            response = inspect.unwrap(books.export_book_list)()
            assert not isinstance(response, tuple), response
            try:
                rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
                assert [row[0] for row in rows[1:]] == [f"Book {book}" for book in expected]
            finally:
                response.close()
