# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for #1233 — the literal word ``None`` typed into a
custom column clears the column instead of being stored.

Symptom (both the classic editor and the new UI, since both write through
the same core): type exactly ``None`` into a notes/comments custom column
and save. The value is not stored — the column is emptied.

Root cause: ``cps/editbooks.py::edit_cc_data_value`` mapped the literal
string to Python ``None`` *before* it looked at the column's datatype::

    if to_save[cc_string] == 'None':
        to_save[cc_string] = None

``None`` is the unset sentinel emitted by the yes/no ``<select>`` in
``cps/templates/book_edit.html`` (``<option value="None">``) — that select
is the only control in the app that emits it. Because the check ran ahead
of the datatype dispatch, a ``comments`` column, whose value is free prose,
lost a legitimate entry.

The fix scopes the sentinel so it no longer applies to ``comments``. It is
deliberately kept for ``int``/``float``/``datetime``: no UI control emits
``None`` for those (number inputs cannot submit it, and the datepicker's
delete button clears via ``''``, which never reaches this function), so
there the string is unparseable input and clearing stays the right
fallback. Dropping it there would instead store the text ``None`` in a
numeric column, or turn a date into ``DEFAULT_PUBDATE``.

These tests call the real ``edit_cc_data_value`` with a stubbed session,
and let the real ``clean_string`` sanitizer run, so they pin the
user-visible outcome: what value ends up on the row.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, patch

import cps.editbooks as editbooks

pytestmark = pytest.mark.unit


class _FakeCC:
    """Stand-in for a generated ``db.cc_classes[id]`` row."""

    def __init__(self, value=None, book=None):
        self.value = value
        self.book = book
        self.books = []


class _Recorder:
    """Captures what the write path did to the session."""

    def __init__(self):
        self.added = []
        self.deleted = []

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        pass


def _call(datatype, submitted, existing=None, column_id=7):
    """Run ``edit_cc_data_value`` for one column and report the outcome.

    Returns ``(changed, outcome)`` where ``outcome`` is one of:
      ``("created", value)``  a new cc row was added with ``value``
      ``("updated", value)``  the existing row's ``value`` was set
      ``("cleared", None)``   the existing row was removed and deleted
      ``("noop", None)``      nothing happened
    """
    cc_string = "custom_column_%d" % column_id
    c = SimpleNamespace(id=column_id, datatype=datatype)
    to_save = {cc_string: submitted}

    recorder = _Recorder()
    calibre_db = MagicMock()
    calibre_db.session = recorder

    if existing is None:
        rows = []
        cc_db_value = None
    else:
        rows = [_FakeCC(value=existing)]
        cc_db_value = existing

    book = SimpleNamespace(**{cc_string: rows})

    with patch.object(editbooks, "calibre_db", calibre_db), \
            patch.object(editbooks.db, "cc_classes", {column_id: _FakeCC}), \
            patch.object(editbooks, "log", MagicMock()):
        changed, _ = editbooks.edit_cc_data_value(
            1, book, c, to_save, cc_db_value, cc_string
        )

    if recorder.added:
        return changed, ("created", recorder.added[0].value)
    if recorder.deleted:
        return changed, ("cleared", None)
    if existing is not None and rows and rows[0].value != existing:
        return changed, ("updated", rows[0].value)
    return changed, ("noop", None)


class TestCommentsColumnKeepsLiteralNone:
    """The bug: a comments column must store the word ``None`` verbatim."""

    def test_none_is_stored_on_an_empty_comments_column(self):
        changed, outcome = _call("comments", "None")
        assert outcome == ("created", "None"), (
            "typing 'None' into an empty comments column must store the text, "
            "not leave the column empty; got %r" % (outcome,)
        )
        assert changed is True

    def test_none_replaces_an_existing_comments_value(self):
        changed, outcome = _call("comments", "None", existing="a real note")
        assert outcome == ("updated", "None"), (
            "typing 'None' over an existing note must save it, not erase the "
            "column; got %r" % (outcome,)
        )
        assert changed is True

    def test_none_inside_a_sentence_is_untouched(self):
        changed, outcome = _call("comments", "None of these editions match.")
        assert outcome == ("created", "None of these editions match.")
        assert changed is True

    def test_ordinary_comments_text_still_saves(self):
        changed, outcome = _call("comments", "Pages: 412")
        assert outcome == ("created", "Pages: 412")
        assert changed is True

    def test_comments_are_still_sanitized(self):
        """The fix must not bypass clean_string on the comments branch."""
        changed, outcome = _call(
            "comments", "None<script>alert(1)</script>"
        )
        kind, value = outcome
        assert kind == "created"
        assert "None" in value
        assert "<script>" not in value, (
            "comments must still run through clean_string; got %r" % (value,)
        )


class TestBoolSentinelPreserved:
    """``None`` is the unset option of the yes/no select — keep it working."""

    def test_bool_none_still_clears_an_existing_value(self):
        changed, outcome = _call("bool", "None", existing=1)
        assert outcome == ("cleared", None), (
            "the yes/no select's blank option must still clear the column; "
            "got %r" % (outcome,)
        )
        assert changed is True

    def test_bool_none_on_an_empty_column_is_a_noop(self):
        changed, outcome = _call("bool", "None")
        assert outcome == ("noop", None)
        assert changed is False

    def test_bool_true_stores_one(self):
        changed, outcome = _call("bool", "True")
        assert outcome == ("created", 1)

    def test_bool_false_stores_zero(self):
        changed, outcome = _call("bool", "False")
        assert outcome == ("created", 0)


class TestNumericAndDateSentinelPreserved:
    """No UI control emits ``None`` here, so clearing stays the fallback.

    Dropping the sentinel for these would store the text ``None`` in a
    numeric column, or (for datetime) fall through ``strptime`` into
    ``DEFAULT_PUBDATE`` — both worse than clearing.
    """

    @pytest.mark.parametrize("datatype", ["int", "float"])
    def test_numeric_none_still_clears(self, datatype):
        changed, outcome = _call(datatype, "None", existing=5)
        assert outcome == ("cleared", None), (
            "%s column: 'None' must not be stored as text; got %r"
            % (datatype, outcome)
        )
        assert changed is True

    def test_datetime_none_still_clears(self):
        changed, outcome = _call(
            "datetime", "None", existing=datetime(2020, 1, 1)
        )
        assert outcome == ("cleared", None), (
            "datetime column: 'None' must clear, not become DEFAULT_PUBDATE; "
            "got %r" % (outcome,)
        )
        assert changed is True

    @pytest.mark.parametrize("datatype", ["int", "float"])
    def test_numeric_values_still_save(self, datatype):
        changed, outcome = _call(datatype, "42")
        assert outcome == ("created", "42")

    def test_datetime_value_still_parses(self):
        changed, outcome = _call("datetime", "2026-07-30")
        kind, value = outcome
        assert kind == "created"
        assert value == datetime(2026, 7, 30)


class TestSentinelScopeIsDatatypeAware:
    """Pin the shape of the guard so the broad form cannot come back."""

    def test_sentinel_check_is_not_datatype_blind(self):
        import inspect

        src = inspect.getsource(editbooks.edit_cc_data_value)
        assert "if to_save[cc_string] == 'None':" not in src, (
            "the 'None' sentinel must be datatype-aware — a bare "
            "`if to_save[cc_string] == 'None':` ahead of the datatype "
            "dispatch is the #1233 regression"
        )
        assert "comments" in src
