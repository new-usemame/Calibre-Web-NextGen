# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom columns must be editable through /api/v1 (#997, #1230).

The SPA renders custom columns on the book page but had no way to change one:
``update_metadata`` iterates a fixed ``EDITABLE_FIELDS`` allowlist, so a
``custom_column_7`` key in the request body was dropped and the endpoint still
answered ``200`` with no ``errors`` entry -- a save that looked like it worked
and changed nothing. Users fell back to the classic editor to type a page count
(reported independently by two people ten days apart).

The write core was already there: ``edit_book_param`` has a
``custom_column_`` branch feeding ``edit_single_cc_data``, which the classic
inline table editor has always used. Only the API layer never routed to it.

These pin the routing and the value contract in both directions. The value
strings here are not arbitrary -- they are what ``edit_cc_data`` parses
(``''`` clears a value, ``'True'``/``'False'`` for bool, ``%Y-%m-%d`` for
datetime, half-star 0-5 for rating because ``edit_cc_data_string`` multiplies
by two, comma-joined for ``is_multiple``). Serialization that does not round
trip through that parser is the failure mode worth catching.
"""
import inspect
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _editor(role_edit=True, anon=False):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon, name="ed",
                           role_edit=lambda: role_edit, role_delete_books=lambda: True, id=1)


def _column(col_id, datatype, label="pages", name="Pages", is_multiple=False, display=None):
    return SimpleNamespace(id=col_id, label=label, name=name, datatype=datatype,
                           is_multiple=is_multiple, display=display,
                           get_display_dict=lambda: json.loads(display or "{}"))


def _book(book_id=5, **custom):
    """A book stub carrying whatever custom_column_N relationships are asked for.

    Values are passed as lists of row stubs, matching the SQLAlchemy
    relationship shape ``edit_cc_data`` and the serializers read.
    """
    base = dict(id=book_id, title="T", authors=[], series=[], series_index=1.0,
                tags=[], publishers=[], languages=[], comments=[], ratings=[],
                identifiers=[], pubdate=None)
    base.update(custom)
    return SimpleNamespace(**base)


def _row(value):
    return SimpleNamespace(value=value)


_SUCCESS = flask.Response(json.dumps({"success": True}), mimetype="application/json")


# ── write routing: the actual regression ─────────────────────────────────────

@pytest.mark.unit
def test_custom_column_key_reaches_the_edit_core():
    """A custom_column_N key must be applied, not silently dropped."""
    from cps.api import edit as mod
    col = _column(7, "int")
    with _ctx("/api/v1/books/5/metadata", body={"custom_column_7": 250}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: _book(custom_column_7=[]),
                 get_cc_columns=lambda *a, **k: [col])), \
             patch.object(mod, "edit_book_param", return_value=_SUCCESS) as core, \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    called = [(c.args[0], c.args[1]["value"]) for c in core.call_args_list]
    assert ("custom_column_7", "250") in called, \
        "custom_column_7 never reached edit_book_param -- the save is a no-op"
    assert resp.status_code == 200
    assert "errors" not in resp.get_json()


@pytest.mark.unit
def test_unknown_custom_column_is_an_error_not_a_silent_success():
    """edit_single_cc_data no-ops on an id it cannot find, so the API must
    reject it rather than answer 200 for a write that did nothing."""
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/metadata", body={"custom_column_99": "x"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: _book(),
                 get_cc_columns=lambda *a, **k: [_column(7, "int")])), \
             patch.object(mod, "edit_book_param", return_value=_SUCCESS) as core, \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    assert core.call_count == 0, "an unknown column id must not be written"
    assert "custom_column_99" in resp.get_json().get("errors", {})


@pytest.mark.unit
def test_absent_custom_column_is_not_written():
    """Only keys the client actually sent get applied -- an omitted column
    keeps its value instead of being cleared."""
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/metadata", body={"title": "New"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: _book(custom_column_7=[_row(120)]),
                 get_cc_columns=lambda *a, **k: [_column(7, "int")])), \
             patch.object(mod, "edit_book_param", return_value=_SUCCESS) as core, \
             patch.object(mod, "get_locale", return_value="en"):
            inspect.unwrap(mod.update_metadata)(5)

    assert [c.args[0] for c in core.call_args_list] == ["title"]


@pytest.mark.unit
def test_custom_column_failure_is_surfaced_per_field():
    from cps.api import edit as mod
    failure = flask.Response(json.dumps({"success": False, "msg": "Database error: locked"}),
                             mimetype="application/json")
    with _ctx("/api/v1/books/5/metadata", body={"custom_column_7": "9"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: _book(custom_column_7=[]),
                 get_cc_columns=lambda *a, **k: [_column(7, "int")])), \
             patch.object(mod, "edit_book_param", return_value=failure), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    assert "locked" in resp.get_json()["errors"]["custom_column_7"]


@pytest.mark.unit
def test_custom_column_write_requires_the_edit_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/metadata", body={"custom_column_7": "9"}):
        with patch.object(mod, "current_user", _editor(role_edit=False)):
            resp = inspect.unwrap(mod.update_metadata)(5)
    assert resp[1] == 403


# ── outbound value contract (what edit_cc_data parses) ───────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    (250, "250"),
    (3.5, "3.5"),
    ("Hardback", "Hardback"),
    (True, "True"),
    (False, "False"),
    (None, ""),            # explicit null clears, matching '' in the classic form
    (["a", "b"], "a, b"),  # is_multiple arrives as a list from the SPA
])
def test_write_value_normalization(raw, expected):
    from cps.api import edit as mod
    assert mod._custom_write_value(raw) == expected


@pytest.mark.unit
def test_bool_write_value_is_not_python_truthiness():
    """'False' must survive as the string edit_cc_data_value compares against;
    a plain str(0) or a falsy-collapse would clear the column instead."""
    from cps.api import edit as mod
    assert mod._custom_write_value(False) == "False"
    assert mod._custom_write_value(False) != ""


# ── inbound value contract (seeding the form) ────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("datatype,rows,expected", [
    ("int", [_row(250)], "250"),
    ("float", [_row(3.5)], "3.5"),
    ("text", [_row("Hardback")], "Hardback"),
    ("comments", [_row("<p>hi</p>")], "<p>hi</p>"),
    ("enumeration", [_row("Read")], "Read"),
    ("bool", [_row(True)], "True"),
    ("bool", [_row(False)], "False"),
    ("int", [], ""),
    ("int", [_row(None)], ""),
])
def test_read_value_serialization(datatype, rows, expected):
    from cps.api import edit as mod
    col = _column(7, datatype)
    book = _book(**{"custom_column_7": rows})
    assert mod._custom_column_value(book, col) == expected


@pytest.mark.unit
def test_rating_reads_as_half_stars_and_round_trips():
    """calibre stores 0-10; the classic form shows value/2 and multiplies back
    on save. Emitting the raw 8 here would double the rating on every save."""
    from cps.api import edit as mod
    col = _column(7, "rating")
    assert mod._custom_column_value(_book(custom_column_7=[_row(8)]), col) == "4"
    assert mod._custom_column_value(_book(custom_column_7=[_row(7)]), col) == "3.5"
    # round trip: what we emit, parsed back by edit_cc_data_string's rule
    assert int(float("4") * 2) == 8
    assert int(float("3.5") * 2) == 7


@pytest.mark.unit
def test_datetime_reads_as_iso_date_and_sentinel_reads_blank():
    from cps.api import edit as mod
    col = _column(7, "datetime")
    got = mod._custom_column_value(_book(custom_column_7=[_row(datetime(2024, 3, 9, 14, 30))]), col)
    assert got == "2024-03-09"          # the %Y-%m-%d edit_cc_data_value strptimes
    # calibre's year-101 "no date" sentinel must not render as 0101-01-01
    assert mod._custom_column_value(_book(custom_column_7=[_row(datetime(101, 1, 1))]), col) == ""


@pytest.mark.unit
def test_multiple_value_column_reads_comma_joined():
    """edit_cc_data splits is_multiple input on ',' -- so the seed value has to
    be comma-joined or the form round-trips a multi-value column into one blob."""
    from cps.api import edit as mod
    col = _column(7, "text", is_multiple=True)
    book = _book(custom_column_7=[_row("sci-fi"), _row("hugo winner")])
    assert mod._custom_column_value(book, col) == "sci-fi, hugo winner"


# ── definitions payload ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_metadata_payload_carries_definitions_and_values():
    from cps.api import edit as mod
    col = _column(7, "int", label="pages", name="Pages")
    book = _book(custom_column_7=[_row(250)])
    with patch.object(mod, "calibre_db", SimpleNamespace(get_cc_columns=lambda *a, **k: [col])), \
         patch.object(mod, "get_locale", return_value="en"):
        payload = mod._editable_metadata(book)

    assert "custom_columns" in payload, "the SPA cannot render a field it is never told about"
    entry = payload["custom_columns"][0]
    assert entry["key"] == "custom_column_7"          # the POST key, kept server-side
    assert entry["name"] == "Pages" and entry["datatype"] == "int"
    assert entry["value"] == "250" and entry["is_multiple"] is False


@pytest.mark.unit
def test_enumeration_ships_its_allowed_values():
    """enum options only existed behind the classic /ajax/getcustomenum route,
    so without this the SPA can only render a free-text box for a fixed set."""
    from cps.api import edit as mod
    col = _column(3, "enumeration", label="status", name="Status",
                  display=json.dumps({"enum_values": ["To read", "Reading", "Read"]}))
    with patch.object(mod, "calibre_db", SimpleNamespace(get_cc_columns=lambda *a, **k: [col])), \
         patch.object(mod, "get_locale", return_value="en"):
        entry = mod._editable_metadata(_book(custom_column_3=[]))["custom_columns"][0]
    assert entry["enum_values"] == ["To read", "Reading", "Read"]


@pytest.mark.unit
def test_unparseable_enum_display_does_not_break_the_editor():
    from cps.api import edit as mod
    col = _column(3, "enumeration", display="{not json")
    with patch.object(mod, "calibre_db", SimpleNamespace(get_cc_columns=lambda *a, **k: [col])), \
         patch.object(mod, "get_locale", return_value="en"):
        entry = mod._editable_metadata(_book(custom_column_3=[]))["custom_columns"][0]
    assert entry["enum_values"] == []


@pytest.mark.unit
def test_unreadable_custom_column_schema_degrades_to_empty():
    """#1153's lesson: supplementary fields must not take the editor down.
    get_cc_columns already returns [] on an unreadable schema; hold that."""
    from cps.api import edit as mod
    with patch.object(mod, "calibre_db", SimpleNamespace(get_cc_columns=lambda *a, **k: [])), \
         patch.object(mod, "get_locale", return_value="en"):
        payload = mod._editable_metadata(_book())
    assert payload["custom_columns"] == []


@pytest.mark.unit
def test_read_status_column_stays_editable():
    """The classic editor offers the configured read-status column; filtering it
    out here would quietly drop a field users have always been able to set."""
    from cps.api import edit as mod
    seen = {}

    def _get_cc_columns(cfg, filter_config_custom_read=False):
        seen["filtered"] = filter_config_custom_read
        return []

    with patch.object(mod, "calibre_db", SimpleNamespace(get_cc_columns=_get_cc_columns)), \
         patch.object(mod, "get_locale", return_value="en"):
        mod._editable_metadata(_book())
    assert seen["filtered"] is False
