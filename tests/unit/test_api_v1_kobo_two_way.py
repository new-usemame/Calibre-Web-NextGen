# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 0 SPA API for Kobo two-way annotation preferences.

WHY: this surface writes *preference* state over a feature whose sync path is
deliberately dead. Two failure classes must be impossible: (1) the preference
round-trip breaking (a user believes they opted in/out or picked books and
the server stored something else), and (2) the API touching the dead switch —
promoting a book to a serving state, calling the gate evaluator, or importing
the proxy path. DB writes are mocked like test_api_v1_account.py; the focus
is endpoint logic and the static no-wiring guarantee (POSITIVE CONTROL: the
AST scan proves 'authoritative'/'seeding' never appear as written values and
gates_allow is never called).
"""

import ast
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _user(**kw):
    defaults = dict(
        is_authenticated=True, is_anonymous=False, id=1, name="alice",
        kobo_two_way_annotation_sync=False,
        kobo_two_way_annotation_scope="all",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _book_row(**kw):
    defaults = dict(
        book_id=3, authority_status="unseeded", opaque_content_status="unknown",
        quarantine_reason=None, seeded_at=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _session(rows, first=None):
    """Mock ub.session covering every query chain this module uses."""
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value.order_by.return_value.all.return_value = rows
    query.filter.return_value.all.return_value = rows
    query.filter.return_value.first.return_value = first
    return session


def _patches(mod, user, session, **overrides):
    """The standard patch stack: user, session, config, env override, titles."""
    return [
        patch.object(mod, "current_user", user),
        patch.object(mod, "ub", SimpleNamespace(
            session=session,
            KoboAnnotationBookState=overrides.get("model", MagicMock()),
        )),
        patch.object(mod, "config", SimpleNamespace(
            config_kobo_two_way_annotation_sync=overrides.get("instance", False),
            config_kobo_sync=overrides.get("kobo", 1),
        )),
        patch.object(mod, "emergency_override_disables",
                     return_value=overrides.get("emergency", False)),
        patch.object(mod, "_book_titles", return_value=overrides.get("titles", {3: "A Title"})),
    ]


def _enter(mod, patches):
    for p in patches:
        p.start()
    return [p.stop for p in patches]


# ── auth gating ──────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("view,method,body", [
    ("get_kobo_two_way_annotations", "GET", None),
    ("update_kobo_two_way_annotations", "POST", {"enabled": True}),
    ("update_kobo_two_way_book", "POST", {"book_id": 3, "enabled": False}),
])
def test_anonymous_gets_401(view, method, body):
    from cps.api import kobo_two_way as mod
    with _ctx("/api/v1/account/kobo-two-way-annotations", method=method, body=body):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(getattr(mod, view))()
    assert resp[1] == 401


# ── GET shape ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_serializes_flags_and_book_states():
    from cps.api import kobo_two_way as mod
    rows = [_book_row(book_id=3), _book_row(book_id=4, authority_status="disabled",
                                            opaque_content_status="present",
                                            quarantine_reason="seed_color_mismatch")]
    session = _session(rows)
    stops = _enter(mod, _patches(mod, _user(
        kobo_two_way_annotation_sync=True, kobo_two_way_annotation_scope="selected",
    ), session, instance=True, titles={3: "A Title", 4: None}))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", method="GET"):
            resp = inspect.unwrap(mod.get_kobo_two_way_annotations)()
        payload = json.loads(resp.get_data())
    finally:
        for stop in stops:
            stop()
    assert payload["enabled"] is True
    assert payload["scope"] == "selected"
    assert payload["instance_enabled"] is True
    assert payload["emergency_disabled"] is False
    assert payload["kobo_available"] is True
    assert payload["books"][0] == {
        "book_id": 3, "title": "A Title", "authority_status": "unseeded",
        "opaque_content_status": "unknown", "quarantine_reason": None,
        "seeded_at": None, "enabled": True, "can_toggle": True,
    }
    assert payload["books"][1]["enabled"] is False
    assert payload["books"][1]["title"] is None
    assert payload["books"][1]["opaque_content_status"] == "present"
    assert payload["books"][1]["quarantine_reason"] == "seed_color_mismatch"


@pytest.mark.unit
def test_get_scope_defaults_to_all_when_unset_or_unknown():
    from cps.api import kobo_two_way as mod
    session = _session([])
    stops = _enter(mod, _patches(mod, _user(kobo_two_way_annotation_scope=None), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", method="GET"):
            payload = json.loads(inspect.unwrap(mod.get_kobo_two_way_annotations)().get_data())
    finally:
        for stop in stops:
            stop()
    assert payload["scope"] == "all"


@pytest.mark.unit
def test_book_titles_degrade_when_library_unavailable():
    from cps.api import kobo_two_way as mod
    from sqlalchemy.exc import SQLAlchemyError
    broken = SimpleNamespace(session=MagicMock(
        query=MagicMock(side_effect=SQLAlchemyError("boom"))))
    with patch.object(mod, "calibre_db", broken):
        assert mod._book_titles([3]) == {}
    assert mod._book_titles([]) == {}


# ── POST settings ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_post_rejects_invalid_scope():
    from cps.api import kobo_two_way as mod
    session = _session([])
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations",
                  body={"scope": "sometimes"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 400
    assert not session.commit.called


@pytest.mark.unit
def test_post_writes_opt_in_and_scope():
    from cps.api import kobo_two_way as mod
    user = _user()
    session = _session([])
    stops = _enter(mod, _patches(mod, user, session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations",
                  body={"enabled": True, "scope": "selected"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp.status_code == 200
    assert user.kobo_two_way_annotation_sync is True
    assert user.kobo_two_way_annotation_scope == "selected"
    assert session.commit.called


@pytest.mark.unit
def test_switch_to_selected_pauses_unseeded_books_only():
    """Default-deny: entering 'selected' must not let legacy/backfill
    'unseeded' rows read as explicit picks. Pipeline evidence rows
    ('quarantined' here) stay untouched."""
    from cps.api import kobo_two_way as mod
    unseeded = _book_row(book_id=3)
    quarantined = _book_row(book_id=4, authority_status="quarantined")
    session = _session([quarantined])
    # The bulk-pause query (filter().all()) returns only unseeded rows.
    session.query.return_value.filter.return_value.all.return_value = [unseeded]
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", body={"scope": "selected"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp.status_code == 200
    assert unseeded.authority_status == "disabled"
    assert quarantined.authority_status == "quarantined"


@pytest.mark.unit
def test_reselecting_selected_does_not_pause_again():
    """Idempotent: POSTing scope 'selected' while already 'selected' must not
    bulk-disable (a picked book would otherwise flip back off)."""
    from cps.api import kobo_two_way as mod
    picked = _book_row(book_id=3)  # user picked it back to 'unseeded'
    session = _session([picked])
    stops = _enter(mod, _patches(mod, _user(kobo_two_way_annotation_scope="selected"), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", body={"scope": "selected"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp.status_code == 200
    assert picked.authority_status == "unseeded"


@pytest.mark.unit
def test_switch_back_to_all_keeps_opt_outs():
    """An opt-out is user data: returning to 'all' must not resurrect it."""
    from cps.api import kobo_two_way as mod
    opted_out = _book_row(book_id=3, authority_status="disabled")
    session = _session([opted_out])
    stops = _enter(mod, _patches(mod, _user(kobo_two_way_annotation_scope="selected"), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", body={"scope": "all"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp.status_code == 200
    assert opted_out.authority_status == "disabled"


# ── POST per-book ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_book_disable_and_reenable_round_trip():
    from cps.api import kobo_two_way as mod
    row = _book_row()
    session = _session([], first=row)
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations/books",
                  body={"book_id": 3, "enabled": False}):
            resp = inspect.unwrap(mod.update_kobo_two_way_book)()
        assert resp.status_code == 200
        assert row.authority_status == "disabled"
        with _ctx("/api/v1/account/kobo-two-way-annotations/books",
                  body={"book_id": 3, "enabled": True}):
            resp = inspect.unwrap(mod.update_kobo_two_way_book)()
        assert resp.status_code == 200
        assert row.authority_status == "unseeded"
        assert row.quarantine_reason is None
    finally:
        for stop in stops:
            stop()


@pytest.mark.unit
def test_book_toggle_404_without_state_row():
    from cps.api import kobo_two_way as mod
    session = _session([], first=None)
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations/books",
                  body={"book_id": 99, "enabled": False}):
            resp = inspect.unwrap(mod.update_kobo_two_way_book)()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 404
    assert not session.commit.called


@pytest.mark.unit
@pytest.mark.parametrize("status", ["seeding", "authoritative", "quarantined"])
def test_book_toggle_409_on_pipeline_evidence(status):
    from cps.api import kobo_two_way as mod
    row = _book_row(authority_status=status)
    session = _session([], first=row)
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations/books",
                  body={"book_id": 3, "enabled": False}):
            resp = inspect.unwrap(mod.update_kobo_two_way_book)()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 409
    assert row.authority_status == status
    assert not session.commit.called


@pytest.mark.unit
@pytest.mark.parametrize("body", [
    {"enabled": True},                    # missing book_id
    {"book_id": "3", "enabled": True},    # string id
    {"book_id": True, "enabled": True},   # bool is an int subclass
    {"book_id": 0, "enabled": True},      # non-positive
    {"book_id": 3},                       # missing enabled
    {"book_id": 3, "enabled": "yes"},     # non-boolean enabled
])
def test_book_toggle_rejects_malformed_bodies(body):
    from cps.api import kobo_two_way as mod
    session = _session([])
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations/books", body=body):
            resp = inspect.unwrap(mod.update_kobo_two_way_book)()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 400


# ── server faults never leak internals (POSITIVE CONTROL) ───────────────────

@pytest.mark.unit
def test_settings_mutation_failure_returns_generic_500():
    """A DB fault mid-mutation is a server error, not bad input: 500 with a
    generic message — never the exception text (schema/paths) or a 400."""
    from cps.api import kobo_two_way as mod
    session = _session([])
    session.query.return_value.filter.return_value.all.side_effect = (
        Exception("SECRET /home/reader/app.db constraint detail"))
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        with _ctx("/api/v1/account/kobo-two-way-annotations", body={"scope": "selected"}):
            resp = inspect.unwrap(mod.update_kobo_two_way_annotations)()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 500
    body = resp[0].get_data(as_text=True)
    assert "SECRET" not in body
    assert json.loads(body)["error"]["code"] == "db_error"
    assert session.rollback.called


@pytest.mark.unit
@pytest.mark.parametrize("view,body", [
    ("update_kobo_two_way_annotations", {"enabled": True}),
    ("update_kobo_two_way_book", {"book_id": 3, "enabled": False}),
])
def test_commit_failure_returns_generic_500(view, body):
    from cps.api import kobo_two_way as mod
    session = _session([], first=_book_row())
    session.commit.side_effect = Exception("SECRET database path /srv/db.sqlite")
    stops = _enter(mod, _patches(mod, _user(), session))
    try:
        path = ("/api/v1/account/kobo-two-way-annotations/books" if "book_id" in body
                else "/api/v1/account/kobo-two-way-annotations")
        with _ctx(path, body=body):
            resp = inspect.unwrap(getattr(mod, view))()
    finally:
        for stop in stops:
            stop()
    assert resp[1] == 500
    payload = json.loads(resp[0].get_data(as_text=True))
    assert "SECRET" not in json.dumps(payload)
    assert payload["error"]["code"] == "db_error"
    assert session.rollback.called


# ── the dead switch stays dead (POSITIVE CONTROL) ────────────────────────────

def _code_string_constants(module):
    """Every string constant in executable code, docstrings excluded."""
    tree = ast.parse(inspect.getsource(module))
    constants = set()

    def body_without_docstring(body):
        if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            return body[1:]
        return body

    class Visitor(ast.NodeVisitor):
        def visit_Module(self, node):
            for child in body_without_docstring(node.body):
                self.visit(child)

        def visit_FunctionDef(self, node):
            for child in body_without_docstring(node.body):
                self.visit(child)

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                constants.add(node.value)

    Visitor().visit(tree)
    return constants


@pytest.mark.unit
def test_api_never_wires_the_sync_path():
    """POSITIVE CONTROL: the preference API must not call the gate evaluator,
    import the proxy/sync path, or write a serving authority state — the
    whole point of Stage 0 is UI over a deliberately dead switch."""
    from cps.api import kobo_two_way as mod

    tree = ast.parse(inspect.getsource(mod))
    imported = set()
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)

    assert "gates_allow" not in called
    assert not any("readingservices" in name or "annotation_sync" in name
                   for name in imported)
    constants = _code_string_constants(mod)
    # Serving states may never appear as written values in this module.
    assert "authoritative" not in constants
    assert "seeding" not in constants


# ── instance capability flag ─────────────────────────────────────────────────
#
# Regression for a defect the SPA e2e suite caught and the unit suite could
# not: every book page fetched the two-way preference endpoint unconditionally,
# so any server without it (an older backend, or the e2e harness which overlays
# only the SPA onto a :dev image) answered 404 and the book page raised a
# console error on EVERY view. The SPA now gates the fetch on this flag, so the
# flag has to exist and has to track the admin's switch.

@pytest.mark.unit
def test_server_features_exposes_kobo_two_way_setting():
    from types import SimpleNamespace
    from unittest.mock import patch
    from cps.api import auth as mod

    cfg = SimpleNamespace(config_user_hide_enabled=False, config_public_reg=False,
                          config_anonbrowse=False, config_kobo_sync=True,
                          config_kobo_sync_magic_shelves=False,
                          config_kobo_two_way_annotation_sync=True,
                          get_mail_server_configured=lambda: False)
    with patch.object(mod, "config", cfg):
        assert mod._server_features()["kobo_two_way_annotations"] is True

    cfg.config_kobo_two_way_annotation_sync = False
    with patch.object(mod, "config", cfg):
        assert mod._server_features()["kobo_two_way_annotations"] is False


@pytest.mark.unit
def test_server_features_kobo_two_way_defaults_off_when_absent():
    """An older/minimal config must read as off, never fault /me."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from cps.api import auth as mod

    with patch.object(mod, "config",
                      SimpleNamespace(get_mail_server_configured=lambda: False)):
        assert mod._server_features()["kobo_two_way_annotations"] is False
