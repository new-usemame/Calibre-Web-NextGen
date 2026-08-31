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


def _run_bulk_edit(monkeypatch, tmp_path, failure_stage, selections=None):
    from cps import editbooks

    books = {book_id: _book(book_id) for book_id in (1, 2, 3)}
    rename_attempts = []

    def update_dir_structure(book_id, *_args):
        rename_attempts.append(book_id)
        if failure_stage == "rename" and book_id == 2:
            return "forced rename failure"
        return False

    commit = MagicMock()
    if failure_stage == "commit":
        commit.side_effect = [
            None,
            OperationalError("forced commit failure", {}, Exception("forced")),
            None,
        ]
    session = SimpleNamespace(commit=commit, rollback=MagicMock())

    monkeypatch.setattr(editbooks.calibre_db, "get_book", books.get)
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

    return json.loads(result), rename_attempts, session


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
        monkeypatch, tmp_path, failure_stage=None,
    )

    assert payload == {"success": True}
    assert rename_attempts == [1, 2, 3]
    assert session.commit.call_count == 3
    session.rollback.assert_not_called()


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
