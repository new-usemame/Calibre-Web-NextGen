# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1734 — smart-shelf capabilities mirror the classic route permissions."""
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from flask_babel import Babel


def _shelf(**overrides):
    values = dict(
        id=17,
        name="Shared smart shelf",
        icon="🪄",
        is_public=1,
        is_system=False,
        user_id=41,
        kobo_sync=False,
        rules={"condition": "AND", "rules": []},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(user_id=7, *, admin=False, edit_shelfs=False, authenticated=True):
    return SimpleNamespace(
        id=user_id,
        is_authenticated=authenticated,
        role_admin=lambda: admin,
        role_edit_shelfs=lambda: edit_shelfs,
        opds_only_shelves_sync=False,
    )


def _serialize(shelf, user):
    from cps.api import magicshelves

    with patch.object(magicshelves.magic_shelf, "system_magic_shelf_display_name",
                      lambda value: value.name):
        return magicshelves._shelf_item(shelf, user)


class _RouteQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def get(self, shelf_id):
        if shelf_id == self.session.shelf.id:
            return self.session.shelf
        return None

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return None

    def delete(self):
        self.session.deleted_query_entities.append(self.entity)


class _RouteSession:
    def __init__(self, shelf):
        self.shelf = shelf
        self.added = []
        self.deleted = []
        self.deleted_query_entities = []
        self.commits = 0

    def query(self, entity):
        return _RouteQuery(self, entity)

    def add(self, value):
        value.id = 99
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


@pytest.fixture()
def classic_route_app():
    from cps import web

    app = flask.Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    Babel(app)
    app.add_url_rule(
        "/magicshelf/<int:shelf_id>/edit",
        "test-edit-magic-shelf",
        inspect.unwrap(web.edit_magic_shelf),
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/magicshelf/<int:shelf_id>/duplicate",
        "test-duplicate-magic-shelf",
        inspect.unwrap(web.duplicate_magic_shelf),
        methods=["POST"],
    )
    app.add_url_rule(
        "/magicshelf/<int:shelf_id>/delete",
        "test-delete-magic-shelf",
        inspect.unwrap(web.delete_magic_shelf),
        methods=["POST"],
    )
    return app


def _dispatch_classic_route(app, path, method, shelf, user):
    from cps import web

    session = _RouteSession(shelf)
    with patch.object(web, "current_user", user), \
         patch.object(web.ub, "session", session), \
         patch.object(web.ub, "session_commit", session.commit), \
         patch.object(web.ub, "is_opds_magic_shelf_exposed_for_user",
                      return_value=False), \
         patch.object(web, "render_title_template", return_value="edit form"), \
         patch.object(web.config, "config_kobo_sync_magic_shelves", False,
                      create=True), \
         patch.object(web.magic_shelf, "build_rule_schema_for_locale",
                      return_value=[]):
        response = app.test_client().open(path, method=method)
    return response, session


@pytest.mark.unit
def test_admin_non_owner_can_edit_public_magic_shelf():
    data = _serialize(_shelf(), _user(admin=True, edit_shelfs=True))
    assert data["can_edit"] is True


@pytest.mark.unit
def test_plain_non_owner_cannot_edit_public_magic_shelf():
    data = _serialize(_shelf(), _user())
    assert data["can_edit"] is False


@pytest.mark.unit
def test_any_authenticated_viewer_can_duplicate_public_magic_shelf():
    data = _serialize(_shelf(), _user())
    assert data["can_duplicate"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shelf", "user", "expected"),
    [
        (_shelf(is_public=0), _user(user_id=41), True),
        (_shelf(), _user(edit_shelfs=True), True),
        (_shelf(is_public=0), _user(edit_shelfs=True), False),
        (_shelf(), _user(), False),
    ],
    ids=("owner-private", "public-shelf-editor", "private-shelf-editor", "plain-public-viewer"),
)
def test_delete_capability_follows_server_rule(shelf, user, expected):
    assert _serialize(shelf, user)["can_delete"] is expected


@pytest.mark.unit
def test_system_magic_shelf_is_not_deletable():
    data = _serialize(_shelf(is_system=True), _user(user_id=41))
    assert data["can_delete"] is False


@pytest.mark.unit
def test_non_owner_admin_cannot_toggle_magic_shelf_kobo_sync():
    data = _serialize(_shelf(), _user(admin=True, edit_shelfs=True))
    assert data["can_kobo_sync"] is False
    assert data["is_owner"] is False


@pytest.mark.unit
def test_list_and_detail_payloads_include_the_same_capabilities():
    """Exercise both read handlers through Flask request dispatch."""
    from cps.api import magicshelves as mod

    shelf = _shelf()
    user = _user(admin=True, edit_shelfs=True)

    class _Query:
        def get(self, shelf_id):
            return shelf if shelf_id == shelf.id else None

    class _Session:
        def query(self, _model):
            return _Query()

    app = flask.Flask(__name__)
    app.add_url_rule("/api/v1/magicshelves", "magic-shelf-list",
                     inspect.unwrap(mod.list_magic_shelves))
    app.add_url_rule("/api/v1/magicshelf/<int:shelf_id>", "magic-shelf-detail",
                     inspect.unwrap(mod.magic_shelf_books))

    with patch.object(mod, "current_user", user), \
         patch.object(mod, "ub", SimpleNamespace(session=_Session(), MagicShelf=object)), \
         patch.object(mod, "config", SimpleNamespace(config_books_per_page=20)), \
         patch.object(mod.magic_shelf, "get_visible_magic_shelves_for_user",
                      return_value=[shelf]), \
         patch.object(mod.magic_shelf, "build_query_from_rules", return_value=None), \
         patch.object(mod.magic_shelf, "system_magic_shelf_display_name",
                      lambda value: value.name):
        client = app.test_client()
        list_item = client.get("/api/v1/magicshelves").get_json()["items"][0]
        detail = client.get(f"/api/v1/magicshelf/{shelf.id}").get_json()

    expected = {
        "can_edit": True,
        "can_delete": True,
        "can_duplicate": True,
        "can_kobo_sync": False,
    }
    assert {key: list_item[key] for key in expected} == expected
    assert {key: detail[key] for key in expected} == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shelf", "user", "expected_status"),
    [
        (_shelf(is_public=0, user_id=7), _user(), 200),
        (_shelf(user_id=41), _user(admin=True), 200),
        (_shelf(user_id=41), _user(), 403),
        (_shelf(is_system=True, user_id=7), _user(), 200),
    ],
    ids=("owner", "admin-non-owner", "plain-non-owner", "system-shelf-owner"),
)
def test_edit_magic_shelf_route_enforces_real_capability(
        classic_route_app, shelf, user, expected_status):
    response, _session = _dispatch_classic_route(
        classic_route_app,
        f"/magicshelf/{shelf.id}/edit",
        "GET",
        shelf,
        user,
    )
    assert response.status_code == expected_status


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shelf", "user", "expected_status"),
    [
        (_shelf(is_public=0, user_id=7), _user(), 200),
        (_shelf(is_public=0, user_id=41), _user(admin=True), 403),
        (_shelf(is_public=1, user_id=41), _user(), 200),
        (_shelf(is_public=1, is_system=True, user_id=41), _user(), 200),
    ],
    ids=("owner", "admin-non-owner-private", "plain-public-viewer", "system-shelf-viewer"),
)
def test_duplicate_magic_shelf_route_enforces_real_capability(
        classic_route_app, shelf, user, expected_status):
    response, session = _dispatch_classic_route(
        classic_route_app,
        f"/magicshelf/{shelf.id}/duplicate",
        "POST",
        shelf,
        user,
    )
    assert response.status_code == expected_status
    assert bool(session.added) is (expected_status == 200)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shelf", "user", "expected_status"),
    [
        (_shelf(is_public=0, user_id=7), _user(), 200),
        (_shelf(is_public=1, user_id=41), _user(admin=True), 403),
        (_shelf(is_public=1, user_id=41), _user(edit_shelfs=True), 200),
        (_shelf(is_public=1, user_id=41), _user(), 403),
        (_shelf(is_public=0, is_system=True, user_id=7), _user(), 400),
    ],
    ids=("owner", "admin-non-owner", "shelf-editor-non-owner",
         "plain-non-owner", "system-shelf-owner"),
)
def test_delete_magic_shelf_route_distinguishes_authority_from_system_shelf(
        classic_route_app, shelf, user, expected_status):
    response, session = _dispatch_classic_route(
        classic_route_app,
        f"/magicshelf/{shelf.id}/delete",
        "POST",
        shelf,
        user,
    )
    assert response.status_code == expected_status
    assert bool(session.deleted) is (expected_status == 200)


@pytest.mark.unit
def test_classic_routes_and_api_use_shared_permission_helpers():
    from cps import web
    from cps.api import magicshelves

    routes = {
        "can_edit_magic_shelf": inspect.getsource(inspect.unwrap(web.edit_magic_shelf)),
        "can_duplicate_magic_shelf": inspect.getsource(inspect.unwrap(web.duplicate_magic_shelf)),
        "can_delete_magic_shelf": inspect.getsource(inspect.unwrap(web.delete_magic_shelf)),
    }
    serializer_source = inspect.getsource(magicshelves._shelf_item)
    for helper_name, route_source in routes.items():
        assert f"magic_shelf.{helper_name}" in route_source
        assert f"magic_shelf.{helper_name}" in serializer_source


@pytest.mark.unit
def test_spa_gates_each_control_on_its_matching_capability():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2]
              / "frontend/src/pages/MagicShelfView.tsx").read_text()
    assert "{data.can_edit && (" in source
    assert "{data.can_duplicate && (" in source
    assert "{data.can_delete && (" in source
    assert "data.can_kobo_sync && me?.features?.kobo_sync" in source
