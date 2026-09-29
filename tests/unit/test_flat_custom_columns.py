# SPDX-License-Identifier: GPL-3.0-or-later
"""Flat (non-hierarchical) custom columns are browsable (#2170 follow-up).

A tag-like custom column whose stored values only *contain* dots — Dewey
``778.3``, LCC ``QA76.76.C68`` — is not a hierarchy. Gating enumeration on the
hierarchy detector removed such columns from the sidebar, from the profile's
"Show <column> Section" checkbox, from OPDS and from the SPA with no way for a
user to switch them back on. The fix restores them and separates the two modes
at the point of RENDERING instead.

These tests pin the half that is easy to get wrong: a flat value is an opaque
atomic string. Dewey ``778.3`` must appear as one entry, never as a ``778`` parent
with a ``3`` child, and selecting it must match that exact stored value with no
prefix expansion.
"""
import ast
import re
from pathlib import Path
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, foreign, relationship, sessionmaker

from cps import db, hierarchy

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

# Dewey values: flat. A dot inside a classification number, not a path.
DEWEY = ["005.84", "770", "775", "778.3", "778.72", "778.921", "778.993925", "823.92"]


@pytest.fixture()
def library(monkeypatch):
    """One hierarchical column (#2) beside one flat Dewey column (#3).

    Books 1-3 carry Dewey values, 4-5 carry Genre values. Book 3 is filed at
    two Dewey rows, which is what a shared classification looks like.
    """
    base = declarative_base()

    class Subject(base):
        __tablename__ = "custom_column_2"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(String)

    class Ddc(base):
        __tablename__ = "custom_column_3"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(String)

    engine = create_engine("sqlite://")
    # The real `books` table has to exist too: get_hierarchical_tree and
    # get_cc_flat_list both .select_from(Books), so the join names it.
    db.Books.__table__.create(engine)
    db.CustomColumns.__table__.create(engine)
    for table in (Subject, Ddc):
        table.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2, 'subjects', 'Genre', 'text', 1), (3, 'ddc', 'DDC', 'text', 0)"))
        connection.execute(Subject.__table__.insert(), [
            {"id": 1, "book": 4, "value": "Art"},
            {"id": 2, "book": 5, "value": "Art.Painting"}])
        connection.execute(Ddc.__table__.insert(), [
            {"id": 1, "book": 1, "value": "778.3"},
            {"id": 2, "book": 2, "value": "778.72"},
            {"id": 3, "book": 3, "value": "778.3"},
            {"id": 4, "book": 3, "value": "778.993925"},
            {"id": 5, "book": 1, "value": "005.84"}])

    # NOTE ON SCOPE: the new methods are tested at the level below the
    # Books.custom_column_N join. That join is a mapped relationship on a class
    # another test module has already configured, and SQLAlchemy will not
    # reconfigure a mapper to pick up a relationship attached afterwards. The
    # join shape itself is already covered by test_2170's tree tests; what these
    # tests pin is the new logic — the row-to-entry mapping, the ordering, the
    # exact-match filter and the search's choice of value set.
    monkeypatch.setattr(db, "cc_classes", {2: Subject, 3: Ddc})
    # dispose() leaves these as None when no library is open, and the flat-list
    # query only passes the attribute to .join(), which the stub ignores.
    monkeypatch.setattr(db.Books, "custom_column_2", None, raising=False)
    monkeypatch.setattr(db.Books, "custom_column_3", None, raising=False)
    monkeypatch.setattr(db.CalibreDB, "_hier_cache", None, raising=False)
    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    calibre.session = session
    calibre.common_filters = lambda *a, **kw: None
    try:
        yield calibre, session, Ddc
    finally:
        session.close()
        engine.dispose()


def _flat_entries(calibre, rows):
    """Drive get_cc_flat_list's row handling with canned query output.

    The query itself is `select value, count(distinct Books.id) ... group by
    value`; what this exercises is the part written for flat columns — the
    row-to-entry mapping, the blank filter and the ordering.
    """
    query = SimpleNamespace()

    def _join(*a, **kw):
        return query

    def _group_by(*a, **kw):
        return query

    def _filter(*a, **kw):
        return query

    query.select_from = _join
    query.join = _join
    query.group_by = _group_by
    query.filter = _filter
    query.all = lambda: rows
    original = calibre.session
    calibre.session = SimpleNamespace(query=lambda *a, **kw: query)
    try:
        return calibre.get_cc_flat_list(3)
    finally:
        calibre.session = original


# ── the two-mode decision ───────────────────────────────────────────────────


def test_only_the_real_prefix_column_is_hierarchical(library):
    calibre, _, _ = library
    # Art / Art.Painting is a real hierarchy; no Dewey value is a prefix of
    # another, so DDC is flat despite every value containing a dot.
    assert calibre.get_hierarchical_column_ids() == {2}
    assert calibre.is_flat_cc_column(3) is True
    assert calibre.is_flat_cc_column(2) is False
    assert calibre.is_flat_cc_column(99) is False, "an unknown id is not a flat column"


# ── the flat list ───────────────────────────────────────────────────────────


def test_a_flat_value_is_one_entry_and_never_becomes_a_parent(library):
    """The whole point: Dewey 778.3 is a classification, not two levels."""
    calibre, _, _ = library
    entries = _flat_entries(calibre, [(value, 1) for value in DEWEY])
    names = [e["name"] for e in entries]
    assert "778.3" in names
    assert "778" not in names, "778 was never stored — it must not be invented"
    assert names == sorted(DEWEY, key=str.lower), "sorted case-insensitively"
    for entry in entries:
        assert entry["children"] == [], "a flat value has no descendants"
        assert entry["path"] == entry["name"], (
            "path == name == value is what lets the existing tree macros render "
            "a flat list with no template change")
        assert entry["count"] == entry["total_count"], (
            "a flat node has no subtree, so the two counts must agree")


def test_the_flat_list_sorts_numerically_looking_alphabetically_safe(library):
    """Case-insensitive sort, so Dewey reads 770, 775, 778.3 rather than the
    lexicographic order that would put '005.84' after '775'."""
    calibre, _, _ = library
    values = ["770", "005.84", "823.92", "778.3"]
    entries = _flat_entries(calibre, [(v, 1) for v in values])
    assert [e["name"] for e in entries] == ["005.84", "770", "778.3", "823.92"]


def test_the_flat_list_drops_blank_values(library):
    """An empty or whitespace-only row is a value nobody can browse to."""
    calibre, _, _ = library
    entries = _flat_entries(calibre, [("778.3", 2), ("", 1), ("   ", 3), (None, 4)])
    assert [e["name"] for e in entries] == ["778.3"]


def test_the_flat_list_counts_come_from_the_query(library):
    calibre, _, _ = library
    entries = {e["name"]: e for e in
               _flat_entries(calibre, [("778.3", 2), ("778.993925", 1)])}
    assert entries["778.3"]["count"] == 2
    assert entries["778.993925"]["count"] == 1


def test_the_flat_list_honours_a_book_filter(library):
    """The OPDS shelf restriction arrives as book_filter, and it REPLACES
    common_filters() rather than adding to it. Omitting it would leak books
    from a restricted shelf into a value list; applying both would apply the
    shelf restriction twice and quietly re-apply the per-user language and
    read-status filters the caller already resolved.
    """
    calibre, session, Ddc = library
    filtered = []
    query = SimpleNamespace()
    query.select_from = lambda *a, **kw: query
    query.join = lambda *a, **kw: query
    query.group_by = lambda *a, **kw: query
    query.all = lambda: [("778.3", 1)]
    query.filter = lambda arg: (filtered.append(arg), query)[1]
    calibre.session = SimpleNamespace(query=lambda *a, **kw: query)

    common_calls = []
    calibre.common_filters = lambda *a, **kw: (common_calls.append(1), None)[1]

    restricted = session.query(Ddc.id)
    assert calibre.get_cc_flat_list(3, book_filter=restricted) != []
    assert filtered == [restricted], "book_filter must reach the query"
    assert common_calls == [], "book_filter replaces common_filters()"

    filtered.clear()
    assert calibre.get_cc_flat_list(3) != []
    assert len(filtered) == 1 and filtered[0] is None, (
        "with no book_filter the common filter is the one applied")
    assert common_calls == [1]

    filtered.clear()
    common_calls.clear()
    assert calibre.get_cc_flat_list(3, apply_common_filters=False) != []
    assert filtered == [], "apply_common_filters=False opts out entirely"


def test_an_unknown_column_yields_no_entries(library):
    calibre, _, _ = library
    assert calibre.get_cc_flat_list(99) == []


# ── the flat filter ─────────────────────────────────────────────────────────


def test_a_flat_node_matches_only_its_exact_value(library):
    calibre, session, Ddc = library

    def books_for(value):
        return {book for (book,) in session.query(Ddc.book).filter(
            calibre.flat_cc_filter(3, value)).distinct()}

    assert books_for("778.3") == {1, 3}
    assert books_for("778.993925") == {3}
    # No prefix expansion: a value that is a prefix of another is not a parent.
    assert books_for("778") == set()
    assert books_for("77") == set(), "there is no LIKE here to over-match"


# ── search stays a substring search ───────────────────────────────


def test_search_keeps_the_substring_match_on_every_text_column():
    """Advanced search must NOT be narrowed by the browse work (SPA_fixes_01
    finding 1).

    Routing a hierarchical column through ``hierarchical_cc_search_filter``
    changed two things a user could see. A term naming a node started matching
    only that node's exact subtree, so "Computers" stopped finding "Old
    Computers"; and because the term is resolved to a node by an exact-string
    lookup before the fallback runs, "Computers" and "computers" reached
    different code paths and returned different sets.

    The coupling was one branch in one function, so it is pinned at the call
    site rather than through the filter's own tests.
    """
    code = _function_code("cps/search.py", "adv_search_custom_columns")
    assert "is_flat_cc_column" not in code, (
        "search must not branch on the hierarchy detector — whether a column "
        "renders as a tree is the browse surface's decision, not search's")
    assert "hierarchical_cc_search_filter" not in code, (
        "search must not resolve a term to a tree node; that narrowed a "
        "substring search to an exact-subtree one")
    assert code.count("ilike") == 1, (
        "one tag-like substring filter for every text/enumeration column")



# ── enumeration is not gated on hierarchy ────────────────────────────────────


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _function_code(rel: str, name: str) -> str:
    """A function's statements as source, with its docstring stripped.

    Scanning the raw text would let a PROSE mention of "hierarchical" in a
    docstring fail an assertion about the filter, which is exactly the kind of
    false positive that makes a structural test get deleted.
    """
    tree = ast.parse(_source(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
                body = body[1:]          # drop the docstring
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError("%s has no function %s" % (rel, name))


@pytest.mark.parametrize("func", [
    "get_custom_column_sidebar_entries",
    "get_custom_column_visibility_options",
])
def test_enumeration_offers_flat_columns_too(func):
    """The regression itself: these two functions once filtered on
    ``col.id not in hierarchical``, which deleted Dewey/LCC from the sidebar and
    left the user with no checkbox to bring them back."""
    code = _function_code("cps/render_template.py", func)
    assert "datatype not in ('text', 'enumeration')" in code, func
    assert "hierarchical" not in code, (
        "%s must not filter on the hierarchy detector — whether a column "
        "renders as a tree or a flat list is the browse route's decision" % func)


def test_the_opds_catalog_root_offers_flat_columns():
    code = _function_code("cps/opds.py", "get_opds_hierarchy_root_entries")
    assert "col.id in hierarchical" not in code, (
        "a flat column must still be discoverable from an OPDS client")


def test_the_spa_api_offers_flat_columns_and_echoes_the_flag():
    """Two separate contracts: the column list must include a flat column, and
    the response must say which mode it is or the SPA cannot render it."""
    assert "hierarchical" not in _function_code("cps/api/columns.py", "_visible_columns"), (
        "_visible_columns must not filter on the hierarchy detector")

    # ast.unparse normalises quotes, so match the expression, not the literal.
    listing = _function_code("cps/api/columns.py", "list_columns")
    assert "'hierarchical': not calibre_db.is_flat_cc_column(col.id)" in listing

    tree = _function_code("cps/api/columns.py", "column_tree")
    assert "'hierarchical': is_hierarchical" in tree, (
        "the tree response must echo the real flag, not a hardcoded True")

    books = _function_code("cps/api/columns.py", "column_books")
    assert "hierarchical_cc_filter(col_id, node)" in books, (
        "the books endpoint must pass the resolved NODE, not a raw path — "
        "hierarchical_cc_filter reads node['raw_values']")
    assert "flat_cc_filter(col_id, path)" in books


def test_the_api_gates_on_the_categories_section_like_every_other_surface():
    """Hiding Categories in the profile must be honoured by the API too, or it
    is a UI-only change an API client can route straight past."""
    body = _source("cps/api/columns.py")
    assert "_may_browse_columns" in body
    for view in ("list_columns", "column_tree", "column_books"):
        assert "_may_browse_columns" in body[body.index("def %s(" % view):], view


# ── the browse route ────────────────────────────────────────────────────────


def test_the_classic_route_serves_both_modes(library, monkeypatch):
    """The route branches once, on is_flat_cc_column, and a flat path is used
    VERBATIM — canonicalising it would invent a value the rows never held."""
    body = _source("cps/web.py")
    start = body.index("def render_cc_category(")
    render = body[start:body.index("\n# ####", start)]
    assert "is_flat_cc_column" in render
    assert "get_cc_flat_list" in render, "a flat root renders the whole-value list"
    assert "flat_cc_filter" in render, "a flat node is an exact-value match"
    # A flat node must pass no subcategories at all, or index.html renders an
    # empty panel that promises children the value does not have.
    flat_branch = render[render.index("A flat node is an exact-value match"):]
    assert "subcategories=" not in flat_branch.split("# Root:")[0]


# ── path normalisation agrees across the three server surfaces ──────────────


def test_all_three_server_surfaces_normalise_a_path_the_same_way():
    """A '/' is part of a stored value, not a separator — and every surface
    that resolves a node path has to agree on that (SPA_fixes_01 finding 2).

    The API endpoint rewrote '/' to '.' while the classic route and OPDS did
    not. The comment above the API line even said '/' was part of a value. The
    effect was that a node like 'Photography.B/W' resolved to 'Photography.B.W',
    matched no node, and 404'd — a value the classic UI lists fine. A
    one-character divergence between three call sites is exactly what a text
    assertion will not catch a third time, so this pins the *transform* each
    surface applies rather than the presence of a function call.
    """
    import re as _re

    def normaliser(rel, func):
        code = _function_code(rel, func)
        # Every rewrite that is not a plain join_path is a divergence.
        return _re.findall(r"join_path\(\[([^\]]*)\]\)", code), code

    for rel, func in (("cps/web.py", "render_cc_category"),
                      ("cps/opds.py", "feed_cc_category"),
                      ("cps/api/columns.py", "column_books")):
        args, code = normaliser(rel, func)
        assert args, "%s::%s must normalise through join_path" % (rel, func)
        for arg in args:
            assert "replace" not in arg, (
                "%s::%s rewrites the path before joining (%s); a '/' is part of a "
                "stored value, so 'Photography.B/W' must survive intact"
                % (rel, func, arg))
            assert "SEPARATOR" not in arg, (
                "%s::%s substitutes a separator into the path" % (rel, func))


def test_a_path_containing_a_slash_survives_normalisation():
    """The behaviour the parity test above protects, exercised on real values.

    Both of these are stored values in the reference library, and both are
    leaves, so there is no other route to their books.
    """
    for stored in ("Photography.B/W", "Software Development.C/C++",
                   "AC/DC.Live", "Computers.DB"):
        normalised = hierarchy.join_path([stored])
        assert normalised == stored, stored
        # And the node is findable under exactly that path.
        tree = hierarchy.parse_tag_hierarchy([(1, stored)])
        assert hierarchy.get_node_by_path(tree, normalised) is not None, stored
        if "/" in stored:
            # The rewrite that caused the 404 must not be recoverable: with a
            # single value in the tree, the rewritten path names a node that
            # was never stored.
            assert hierarchy.get_node_by_path(
                tree,
                hierarchy.join_path([stored.replace("/", hierarchy.SEPARATOR)])) is None, stored


def test_the_detail_page_links_a_flat_value_instead_of_printing_it(library):
    body = _source("cps/templates/detail.html")
    assert "c.id not in hierarchical_cc_ids" in body, (
        "a flat text value must render as a link to its own browse node")
    # The hierarchical crumb branch must survive — it is correct today.
    assert "c.id in hierarchical_cc_ids" in body
    assert re.search(
        r"if hierarchical_cc_ids and c\.id in hierarchical_cc_ids and '\.' in cc_value", body
    ), "the dotted-crumb branch must still be gated on the hierarchy set"
