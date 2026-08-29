# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 shelves — HTTP envelope + status-code mapping.

The DB-touching shelf core (add_book_to_shelf / remove_book_from_shelf /
delete_shelf_helper) is exercised by the existing cps/shelf.py test suite and
the container verification; here we pin the API layer's own logic: validation,
permission gating, and the mapping from core status codes to HTTP responses.
"""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _shelf(**kw):
    defaults = dict(id=1, name="Favourites", is_public=0, user_id=7,
                    kobo_sync=False, uuid="abc")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── serializer ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_serialize_shelf_shape():
    from cps.api.serializers import serialize_shelf
    out = serialize_shelf(_shelf(is_public=1, kobo_sync=True), count=12, is_owner=True)
    assert out == {
        "id": 1, "name": "Favourites", "is_public": True,
        "is_owner": True, "kobo_sync": True, "count": 12,
    }


# ── create ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_create_shelf_empty_name_400():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves", body={"name": "  "}):
        resp = inspect.unwrap(mod.create_shelf_api)()
    assert resp[1] == 400
    assert json.loads(resp[0].get_data())["error"]["code"] == "invalid_request"


@pytest.mark.unit
def test_create_public_without_role_403():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves", body={"name": "Shared", "is_public": True}):
        with patch.object(mod, "current_user",
                          SimpleNamespace(role_edit_shelfs=lambda: False, id=7)):
            resp = inspect.unwrap(mod.create_shelf_api)()
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "forbidden"


@pytest.mark.unit
def test_create_conflict_409():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves", body={"name": "Dupe"}):
        with patch.object(mod, "current_user",
                          SimpleNamespace(role_edit_shelfs=lambda: True, id=7)), \
             patch.object(mod, "check_shelf_is_unique", return_value=False):
            resp = inspect.unwrap(mod.create_shelf_api)()
    assert resp[1] == 409
    assert json.loads(resp[0].get_data())["error"]["code"] == "conflict"


@pytest.mark.unit
def test_create_ok_201():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves", body={"name": "New"}):
        with patch.object(mod, "current_user",
                          SimpleNamespace(role_edit_shelfs=lambda: True, id=7, is_authenticated=True)), \
             patch.object(mod, "check_shelf_is_unique", return_value=True), \
             patch.object(mod, "ub") as mock_ub:
            mock_ub.Shelf = lambda **kw: _shelf(**kw)
            resp = inspect.unwrap(mod.create_shelf_api)()
    # (Response, 201)
    assert resp[1] == 201
    body = json.loads(resp[0].get_data())
    assert body["name"] == "New" and body["is_owner"] is True


# ── not found ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_detail_shelf_not_found_404():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/99", method="GET"):
        with patch.object(mod, "ub") as mock_ub:
            mock_ub.session.query.return_value.filter.return_value.first.return_value = None
            resp = inspect.unwrap(mod.shelf_detail)(99)
    assert resp[1] == 404


# ── add book — status mapping ────────────────────────────────────────────────

def _add_with_core_status(status, message=None):
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/books/5"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "check_shelf_edit_permissions", return_value=True), \
             patch.object(mod, "add_book_to_shelf", return_value=(status, message)):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            return inspect.unwrap(mod.add_book_to_shelf_api)(1, 5)


@pytest.mark.unit
def test_add_book_ok_200():
    from cps.api import shelves as mod
    resp = _add_with_core_status(mod.SHELF_OK)
    # plain jsonify Response (200)
    assert resp.status_code == 200
    assert json.loads(resp.get_data())["on_shelf"] is True


@pytest.mark.unit
def test_add_book_invalid_book_404():
    from cps.api import shelves as mod
    resp = _add_with_core_status(mod.SHELF_INVALID_BOOK, "bad id")
    assert resp[1] == 404
    assert json.loads(resp[0].get_data())["error"]["code"] == "not_found"


@pytest.mark.unit
def test_add_book_not_in_library_409():
    from cps.api import shelves as mod
    resp = _add_with_core_status(mod.SHELF_NOT_IN_LIBRARY, "add it first")
    assert resp[1] == 409
    assert json.loads(resp[0].get_data())["error"]["code"] == "library_membership_required"


@pytest.mark.unit
def test_add_book_already_present_409():
    from cps.api import shelves as mod
    resp = _add_with_core_status(mod.SHELF_ALREADY_PRESENT, "dupe")
    assert resp[1] == 409
    assert json.loads(resp[0].get_data())["error"]["code"] == "conflict"


@pytest.mark.unit
def test_add_book_forbidden_403():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/books/5"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "check_shelf_edit_permissions", return_value=False):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            resp = inspect.unwrap(mod.add_book_to_shelf_api)(1, 5)
    assert resp[1] == 403


# ── remove book — status mapping ─────────────────────────────────────────────

@pytest.mark.unit
def test_remove_book_ok_204():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/books/5"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "check_shelf_edit_permissions", return_value=True), \
             patch.object(mod, "remove_book_from_shelf", return_value=(mod.SHELF_OK, None)):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            resp = inspect.unwrap(mod.remove_book_from_shelf_api)(1, 5)
    # ("", 204)
    assert resp[1] == 204


@pytest.mark.unit
def test_remove_book_not_present_404():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/books/5"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "check_shelf_edit_permissions", return_value=True), \
             patch.object(mod, "remove_book_from_shelf", return_value=(mod.SHELF_NOT_PRESENT, "gone")):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            resp = inspect.unwrap(mod.remove_book_from_shelf_api)(1, 5)
    assert resp[1] == 404


# ── delete ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_delete_forbidden_403():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/delete"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "delete_shelf_helper", return_value=False):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            resp = inspect.unwrap(mod.delete_shelf_api)(1)
    assert resp[1] == 403


@pytest.mark.unit
def test_delete_ok_204():
    from cps.api import shelves as mod
    with _ctx("/api/v1/shelves/1/delete"):
        with patch.object(mod, "ub") as mock_ub, \
             patch.object(mod, "delete_shelf_helper", return_value=True):
            mock_ub.session.query.return_value.filter.return_value.first.return_value = _shelf()
            resp = inspect.unwrap(mod.delete_shelf_api)(1)
    assert resp[1] == 204


@pytest.mark.unit
def test_shelves_has_reorder_and_series_endpoints():
    """Source-pin: the depth endpoints exist + reuse the shared cores (reorder via
    compute_shelf_positions, add-series queues hardcover sync). Reorder verified
    live in the container; this guards the wiring."""
    import inspect as _inspect
    from cps.api import shelves as mod
    assert callable(mod.reorder_shelf_books_api)
    assert callable(mod.add_series_to_shelf_api)
    reorder_src = _inspect.getsource(mod.reorder_shelf_books_api)
    assert "compute_shelf_positions" in reorder_src
    assert "check_shelf_edit_permissions" in reorder_src
    series_src = _inspect.getsource(mod.add_series_to_shelf_api)
    assert "queue_hardcover_sync" in series_src
    assert "series_index" in series_src
    # create now honours kobo_sync
    assert "kobo_sync" in _inspect.getsource(mod.create_shelf_api)
