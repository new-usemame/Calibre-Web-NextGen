#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kobo must retry collection deltas whose database commit rolled back.

Nickel uploads collection mutations once and retires them after a successful
HTTP response.  ``ub.session_commit()`` deliberately turns a caught transient
database failure into ``False``, so every answer-bearing route must translate
that result into a retryable 5xx rather than acknowledge a write that did not
land (#1318, F-5dcb50 and F-db823b).
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

from cps import kobo


USER = SimpleNamespace(
    id=7,
    kobo_only_shelves_sync=False,
    check_visibility=lambda _permission: True,
)


def _client_for(view, rule, method):
    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.add_url_rule(
        rule,
        view_func=inspect.unwrap(view),
        methods=[method],
    )
    return app.test_client()


def _session_returning(value):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value = query
    query.one_or_none.return_value = value
    query.delete.return_value = 1
    return session


@pytest.mark.unit
def test_tag_create_refuses_success_when_commit_rolled_back(monkeypatch):
    session = _session_returning(None)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)
    monkeypatch.setattr(kobo, "add_items_to_shelf", lambda _items, _shelf: [])

    with patch.object(kobo, "current_user", USER):
        response = _client_for(
            kobo.HandleTagCreate, "/v1/library/tags", "POST",
        ).post("/v1/library/tags", json={"Name": "Queued", "Items": []})

    assert response.status_code == 503


@pytest.mark.unit
def test_tag_rename_refuses_success_when_commit_rolled_back(monkeypatch):
    shelf = SimpleNamespace(id=11, uuid="tag-11", user_id=USER.id, name="Old")
    session = _session_returning(shelf)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)

    with patch.object(kobo, "current_user", USER):
        response = _client_for(
            kobo.HandleTagUpdate, "/v1/library/tags/<tag_id>", "PUT",
        ).put("/v1/library/tags/tag-11", json={"Name": "New"})

    assert response.status_code == 503


@pytest.mark.unit
def test_tag_delete_refuses_success_when_commit_rolled_back(monkeypatch):
    shelf = SimpleNamespace(
        id=11,
        uuid="tag-11",
        user_id=USER.id,
        name="Queued",
        is_public=False,
    )
    session = _session_returning(shelf)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)
    monkeypatch.setattr(kobo.shelf_lib, "check_shelf_edit_permissions", lambda _shelf: True)

    with patch.object(kobo, "current_user", USER), \
            patch.object(kobo.shelf_lib, "current_user", USER):
        response = _client_for(
            kobo.HandleTagUpdate, "/v1/library/tags/<tag_id>", "DELETE",
        ).delete("/v1/library/tags/tag-11")

    assert response.status_code == 503


@pytest.mark.unit
def test_tag_add_item_refuses_success_when_commit_rolled_back(monkeypatch):
    shelf = SimpleNamespace(id=11, uuid="tag-11", user_id=USER.id)
    session = _session_returning(shelf)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)
    monkeypatch.setattr(kobo, "add_items_to_shelf", lambda _items, _shelf: [])
    monkeypatch.setattr(kobo.shelf_lib, "check_shelf_edit_permissions", lambda _shelf: True)

    with patch.object(kobo, "current_user", USER):
        response = _client_for(
            kobo.HandleTagAddItem,
            "/v1/library/tags/<tag_id>/items",
            "POST",
        ).post("/v1/library/tags/tag-11/items", json={"Items": []})

    assert response.status_code == 503


@pytest.mark.unit
def test_tag_remove_item_refuses_success_when_commit_rolled_back(monkeypatch):
    shelf_books = MagicMock()
    shelf = SimpleNamespace(
        id=11,
        uuid="tag-11",
        user_id=USER.id,
        books=shelf_books,
    )
    session = _session_returning(shelf)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)
    monkeypatch.setattr(kobo.shelf_lib, "check_shelf_edit_permissions", lambda _shelf: True)
    monkeypatch.setattr(
        kobo.calibre_db,
        "get_book_by_uuid_for_kobo",
        lambda *_args, **_kwargs: SimpleNamespace(id=42),
    )

    with patch.object(kobo, "current_user", USER):
        response = _client_for(
            kobo.HandleTagRemoveItem,
            "/v1/library/tags/<tag_id>/items/delete",
            "POST",
        ).post(
            "/v1/library/tags/tag-11/items/delete",
            json={"Items": [{
                "Type": "ProductRevisionTagItem",
                "RevisionId": "book-42",
            }]},
        )

    assert response.status_code == 503


@pytest.mark.unit
def test_book_delete_refuses_success_when_commit_rolled_back(monkeypatch):
    book = SimpleNamespace(id=42, uuid="book-42")
    archive = MagicMock()
    remove = MagicMock()
    monkeypatch.setattr(
        kobo.calibre_db,
        "get_book_by_uuid_for_kobo",
        lambda *_args, **_kwargs: book,
    )
    monkeypatch.setattr(kobo.kobo_sync_status, "change_archived_books", archive)
    monkeypatch.setattr(kobo.kobo_sync_status, "remove_synced_book", remove)
    monkeypatch.setattr(kobo.ub, "session_commit", lambda *a, **k: False)

    with patch.object(kobo, "current_user", USER):
        response = _client_for(
            kobo.HandleBookDeletionRequest,
            "/v1/library/<book_uuid>",
            "DELETE",
        ).delete("/v1/library/book-42")

    assert response.status_code == 503
    archive.assert_called_once_with(42, True, commit=False)
    remove.assert_called_once_with(42, commit=False)
