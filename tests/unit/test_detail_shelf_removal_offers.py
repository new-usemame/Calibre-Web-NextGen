# SPDX-License-Identifier: GPL-3.0-or-later
"""The classic book page offers shelf removal only where the server allows it.

``shelf.remove_from_shelf`` refuses a public shelf to a reader without the
"edit public shelves" role, and every private shelf but the reader's own
(``check_shelf_edit_permissions``). The toolbar's "Remove from shelf" menu used
to list every visible shelf holding the book, and to appear whenever any shelf
held it, including another reader's private shelf it could not even list.
"""

import pytest
from bs4 import BeautifulSoup

from tests.fixtures.detail_template import reader, render_detail, shelf

pytestmark = pytest.mark.unit

OWN = shelf(1, "Own", owner=7)
PUBLIC = shelf(2, "Everyone's", owner=1, public=True)
OTHER_READERS_PRIVATE = 3  # holds the book; never in the reader's shelves_access
EDITOR = reader(role_edit_shelfs=lambda: True)


def removal_offers(**kwargs):
    """(toolbar menu shown, its removal targets, shelves whose pill has a remove button)."""
    page = BeautifulSoup(render_detail(shelves_access=[OWN, PUBLIC], **kwargs), "html.parser")
    toolbar = [link["data-href"].rsplit("shelf_id=", 1)[1]
               for link in page.select('#remove-from-shelves a[data-shelf-action="remove"]')]
    pills = [pill["data-shelf-id"] for pill in page.select("#detail-shelves .shelf-pill")
             if pill.select_one(".shelf-remove")]
    return page.select_one("#removeShelfMenu") is not None, toolbar, pills


def test_toolbar_offers_removal_only_from_shelves_the_reader_may_edit():
    everywhere = [1, 2, OTHER_READERS_PRIVATE]
    assert removal_offers(books_shelfs=everywhere) == (True, ["1"], ["1"])
    assert removal_offers(user=EDITOR, books_shelfs=everywhere) == (True, ["1", "2"], ["1", "2"])


def test_no_removal_control_when_no_shelf_holding_the_book_is_editable():
    assert removal_offers(books_shelfs=[2, OTHER_READERS_PRIVATE]) == (False, [], [])
    assert removal_offers(books_shelfs=[OTHER_READERS_PRIVATE]) == (False, [], [])
    assert removal_offers(user=EDITOR, books_shelfs=[OTHER_READERS_PRIVATE]) == (False, [], [])
