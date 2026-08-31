# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for truthful bulk-edit outcomes (#2073)."""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask
import pytest
from sqlalchemy.exc import OperationalError


pytestmark = pytest.mark.unit


def _book(book_id):
    return SimpleNamespace(
        id=book_id,
        title=f"Book {book_id}",
        authors=[SimpleNamespace(name="Author")],
    )


class _ExpiringIdBook:
    def __init__(self, book_id):
        self._book_id = book_id
        self._id_expired = False
        self.title = f"Book {book_id}"
        self.authors = [SimpleNamespace(name="Author")]

    @property
    def id(self):
        if self._id_expired:
            raise AssertionError("book.id accessed after transaction boundary")
        return self._book_id

    def expire_id(self):
        self._id_expired = True


def _run_bulk_edit(
    monkeypatch,
    tmp_path,
    failure_stage,
    selections=None,
    expire_book_ids_on_boundary=False,
    decode_response=True,
):
    from cps import editbooks

    book_factory = _ExpiringIdBook if expire_book_ids_on_boundary else _book
    books = {book_id: book_factory(book_id) for book_id in (1, 2, 3)}
    rename_attempts = []
    active_book = None
    active_book_id = None

    def get_book(book_id):
        nonlocal active_book, active_book_id
        active_book = books.get(book_id)
        active_book_id = book_id
        return active_book

    def update_dir_structure(book_id, *_args):
        rename_attempts.append(book_id)
        if failure_stage == "rename" and book_id == 2:
            return "forced rename failure"
        return False

    def commit_book():
        if failure_stage == "commit" and active_book_id == 2:
            raise OperationalError(
                "forced commit failure", {}, Exception("forced"),
            )
        if expire_book_ids_on_boundary:
            active_book.expire_id()

    def rollback_book():
        if expire_book_ids_on_boundary:
            active_book.expire_id()

    session = SimpleNamespace(
        commit=MagicMock(side_effect=commit_book),
        rollback=MagicMock(side_effect=rollback_book),
    )

    monkeypatch.setattr(editbooks.calibre_db, "get_book", get_book)
    monkeypatch.setattr(editbooks.calibre_db, "session", session)
    monkeypatch.setattr(
        editbooks.helper, "update_dir_structure", update_dir_structure,
    )
    monkeypatch.setattr(editbooks.helper, "mark_book_modified", MagicMock())
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        editbooks.constants, "CWA_METADATA_CHANGE_LOGS_DIR", str(tmp_path / "logs"),
    )
    monkeypatch.setattr(editbooks, "handle_title_on_edit", lambda *_args: True)
    monkeypatch.setattr(
        editbooks,
        "_",
        lambda message, **values: message % values if values else message,
    )

    app = Flask(__name__)
    with app.test_request_context(
        "/ajax/editselectedbooks",
        method="POST",
        json={
            "selections": selections if selections is not None else [1, 2, 3],
            "title": "Updated title",
            "checkA": "false",
        },
    ):
        result = inspect.unwrap(editbooks.edit_selected_books)()

    payload = json.loads(result) if decode_response else result
    return payload, rename_attempts, session


@pytest.mark.parametrize("failure_stage", ["rename", "commit"])
def test_bulk_edit_reports_each_failure_and_continues(
    monkeypatch, tmp_path, failure_stage,
):
    payload, rename_attempts, session = _run_bulk_edit(
        monkeypatch, tmp_path, failure_stage,
    )

    assert payload["success"] is False
    assert payload["successful_books"] == [1, 3]
    assert payload["failed_books"] == [
        {
            "book_id": 2,
            "stage": failure_stage,
            "files_may_be_inconsistent": True,
        },
    ]
    assert "may now be inconsistent" in payload["message"]
    assert rename_attempts == [1, 2, 3]
    assert session.commit.call_count == (2 if failure_stage == "rename" else 3)
    session.rollback.assert_called_once_with()


def test_bulk_edit_keeps_the_legacy_all_success_shape(monkeypatch, tmp_path):
    payload, rename_attempts, session = _run_bulk_edit(
        monkeypatch, tmp_path, failure_stage=None, decode_response=False,
    )

    assert payload == '{"success": true}'
    assert rename_attempts == [1, 2, 3]
    assert session.commit.call_count == 3
    session.rollback.assert_not_called()


@pytest.mark.parametrize("failure_stage", [None, "rename", "commit"])
def test_bulk_edit_captures_book_id_before_transaction_boundary(
    monkeypatch, tmp_path, failure_stage,
):
    selections = [1] if failure_stage is None else [2]
    payload, rename_attempts, _session = _run_bulk_edit(
        monkeypatch,
        tmp_path,
        failure_stage,
        selections=selections,
        expire_book_ids_on_boundary=True,
    )

    if failure_stage is None:
        assert payload == {"success": True}
        assert rename_attempts == [1]
    else:
        assert payload["success"] is False
        assert payload["successful_books"] == []
        assert payload["failed_books"] == [{
            "book_id": 2,
            "stage": failure_stage,
            "files_may_be_inconsistent": True,
        }]


def test_non_numeric_failed_id_is_not_reflected_in_user_message(
    monkeypatch, tmp_path,
):
    hostile_id = '<img src=x onerror="alert(1)">'
    payload, rename_attempts, session = _run_bulk_edit(
        monkeypatch,
        tmp_path,
        failure_stage=None,
        selections=[hostile_id, 1],
    )

    assert payload["success"] is False
    assert hostile_id not in payload["message"]
    assert payload["failed_books"] == [{
        "book_id": hostile_id,
        "stage": "lookup",
        "files_may_be_inconsistent": False,
    }]
    assert payload["successful_books"] == [1]
    assert rename_attempts == [1]
    session.commit.assert_called_once_with()


def test_books_table_surfaces_the_partial_failure_message():
    table_js = (
        Path(__file__).resolve().parents[2] / "cps" / "static" / "js" / "table.js"
    ).read_text(encoding="utf-8")
    callback = table_js.split(
        'url: window.location.pathname + "/../ajax/editselectedbooks"', 1,
    )[1].split("});", 1)[0]

    assert "booTitles.success === false" in callback
    assert "booTitles.message" in callback
    assert "handleListServerResponse" in callback
