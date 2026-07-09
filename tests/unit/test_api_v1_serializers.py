import pytest
from types import SimpleNamespace


@pytest.mark.unit
def test_serialize_book_list_item_full():
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(
        id=7, title="Dune", series_index="1.0", has_cover=1,
        authors=[SimpleNamespace(name="Frank Herbert")],
        series=[SimpleNamespace(name="Dune Chronicles")],
        data=[SimpleNamespace(format="EPUB"), SimpleNamespace(format="PDF")],
    )
    assert serialize_book_list_item(book) == {
        "id": 7, "title": "Dune",
        "authors": ["Frank Herbert"],
        "series": "Dune Chronicles", "series_index": "1.0",
        "cover_url": "/cover/7/sm",
        "formats": ["EPUB", "PDF"],
        "tags": [],
        "read": False,
        "archived": False,
    }


@pytest.mark.unit
def test_serialize_book_list_item_tags():
    # #725: the table view's Tags column needs tag names in the list item.
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(
        id=9, title="T", series_index="1.0", has_cover=0,
        authors=[], series=[], data=[],
        tags=[SimpleNamespace(id=1, name="Science Fiction"),
              SimpleNamespace(id=2, name="Space Opera")],
    )
    assert serialize_book_list_item(book)["tags"] == ["Science Fiction", "Space Opera"]


@pytest.mark.unit
def test_serialize_book_list_item_tags_absent_is_empty():
    # A book with no tags relationship (or none loaded) → empty list, never None.
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(id=10, title="U", series_index="1.0", has_cover=0,
                           authors=[], series=[], data=[])
    assert serialize_book_list_item(book)["tags"] == []


@pytest.mark.unit
def test_serialize_book_list_item_no_cover_no_series():
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(id=3, title="X", series_index="1.0", has_cover=0,
                           authors=[], series=[], data=[])
    out = serialize_book_list_item(book)
    assert out["cover_url"] is None
    assert out["series"] is None
    assert out["authors"] == []
    assert out["formats"] == []
    assert out["read"] is False
    assert out["archived"] is False


@pytest.mark.unit
def test_serialize_book_list_item_read_archived():
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(id=5, title="Y", series_index="1.0", has_cover=0,
                           authors=[], series=[], data=[])
    out = serialize_book_list_item(book, read=True, archived=True)
    assert out["read"] is True
    assert out["archived"] is True


@pytest.mark.unit
def test_serialize_user_roles():
    from cps.api.serializers import serialize_user
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.locale, u.theme = 1, "admin", "en", 1
    u.role = constants.ROLE_ADMIN | constants.ROLE_UPLOAD
    out = serialize_user(u)
    assert out["id"] == 1 and out["name"] == "admin" and out["locale"] == "en" and out["theme"] == 1
    assert out["role"]["admin"] is True
    assert out["role"]["upload"] is True
    assert out["role"]["edit"] is False


@pytest.mark.unit
def test_serialize_book_detail_sanitizes_description_xss():
    """description_html must be sanitized (no executable HTML) — stored-XSS guard."""
    from types import SimpleNamespace
    from cps.api.serializers import serialize_book_detail
    book = SimpleNamespace(
        id=9, title="X", series_index="1.0", has_cover=0, pubdate=None,
        authors=[], series=[], data=[], tags=[], languages=[], publishers=[], identifiers=[],
        comments=[SimpleNamespace(
            text='<p>Safe</p><script>alert(1)</script><img src=x onerror=alert(2)>')],
    )
    out = serialize_book_detail(book)
    assert "<script>" not in out["description_html"]
    assert "<img" not in out["description_html"]   # img tag escaped/neutralized
    assert "<script" not in out["description_html"]
    assert "<p>Safe</p>" in out["description_html"]


def _detail_book(**overrides):
    """A minimal fake Book for serialize_book_detail — all list attrs empty."""
    base = dict(
        id=9, title="X", series_index="1.0", has_cover=0, pubdate=None,
        authors=[], series=[], data=[], tags=[], languages=[], publishers=[],
        identifiers=[], comments=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
def test_serialize_book_detail_rating_present():
    """A rated book exposes its raw 0–10 Calibre rating so the UI can render
    half-stars (9 → 4.5). Parity with the classic detail page's star block."""
    from cps.api.serializers import serialize_book_detail
    book = _detail_book(ratings=[SimpleNamespace(rating=9)])
    out = serialize_book_detail(book)
    assert out["rating"] == 9


@pytest.mark.unit
def test_serialize_book_detail_rating_absent_is_null():
    """An unrated book (no ratings link, or the attr missing entirely) emits
    rating=None rather than 0 — 0 stars and 'not rated' are different states."""
    from cps.api.serializers import serialize_book_detail
    # Empty ratings list.
    assert serialize_book_detail(_detail_book(ratings=[]))["rating"] is None
    # Attribute absent entirely (getattr fallback path).
    assert serialize_book_detail(_detail_book())["rating"] is None


@pytest.mark.unit
def test_serialize_book_list_item_author_pipe_unescaped():
    """Calibre escapes a comma inside a single author name as '|' in the DB
    (e.g. "William H. Keith, Jr." is stored "William H. Keith| Jr."). The list
    serializer must un-escape it so the SPA book cards show a comma, not a pipe
    (fork #730, reported by neontapir). Every other display path (web.py,
    api/browse.py, api/edit.py, api/duplicates.py) already does this replace."""
    from cps.api.serializers import serialize_book_list_item
    book = SimpleNamespace(
        id=1, title="Warstrider", series_index="1.0", has_cover=0,
        authors=[SimpleNamespace(name="William H. Keith| Jr.")],
        series=[], data=[],
    )
    assert serialize_book_list_item(book)["authors"] == ["William H. Keith, Jr."]


@pytest.mark.unit
def test_serialize_book_detail_author_pipe_unescaped():
    """The detail serializer must un-escape the Calibre '|' comma in author
    names too, so the SPA detail page renders "William H. Keith, Jr." instead
    of the raw stored form (fork #730). The author id is preserved for linking."""
    from cps.api.serializers import serialize_book_detail
    book = _detail_book(authors=[SimpleNamespace(id=9, name="William H. Keith| Jr.")])
    assert serialize_book_detail(book)["authors"] == [
        {"id": 9, "name": "William H. Keith, Jr."}
    ]
