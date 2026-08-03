# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1318 — a write that did not land must never be reported as success.

``ub.session_commit`` catches ``OperationalError``/``InvalidRequestError``,
rolls back, logs, and then returns ``""`` — the same value it returns on
success.  The return type simply cannot express failure, so every caller is
structurally unable to notice one.  Three callers already try:

  * ``ub.password_change`` branches on ``session_commit() == ""``, which is
    unconditionally true, so its ``"Failed changing password"`` / ``exit(3)``
    branch is dead code and a failed admin password reset exits 0 claiming
    success;
  * ``kobo_auth.delete_auth_token`` returns the helper's value directly, so a
    rolled-back token revocation answers 200 with an empty body;
  * ``admin.edit_domain`` does the same for a domain edit.

and the two bookmark write routes answer 201/204 unconditionally.  Those are
the hot ones: the classic reader posts a bookmark on every page turn and the
SPA debounces one every 800ms, so under ``database is locked`` the user's
position is rolled back while the client is told it saved — and a client told
201 has no reason to retry.

Second, narrower failure in the same area: ``record_web_reader_progress`` used
to perform the settling ``session.flush()`` itself.  That flush *is* the
caller's bookmark write, but it happened inside the region the routes guard
with ``except Exception: log.warning("Could not share web reader progress")``,
so a genuine bookmark failure was reported in the vocabulary of an optional
progress-sharing failure and then answered 201.  Settling the required write
now belongs to the route that owns it.

Pinned here:
  * ``session_commit`` returns True/False and ``session_flush`` reports the
    same way, both still rolling back and logging;
  * ``session_commit``'s *caught* exception set is unchanged — 80-odd callers
    ignore the return value entirely and must keep their current behaviour,
    including letting an ``IntegrityError`` propagate;
  * every caller that consumes the return value now reacts to a failure;
  * both bookmark routes answer 500 when the write did not land;
  * the settling flush lives in the routes, so a bookmark failure is never
    misattributed to progress sharing.
"""

import ast
import re
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import sessionmaker

from cps import ub

REPO = Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────

def _session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _FailingSession:
    """A session whose commit/flush raises the class the helper catches."""

    def __init__(self, error=None):
        self.error = error or sa_exc.OperationalError("stmt", {}, Exception("database is locked"))
        self.rolled_back = False

    def commit(self):
        raise self.error

    def flush(self):
        raise self.error

    def rollback(self):
        self.rolled_back = True


# ── the helper's contract ────────────────────────────────────────────────────

@pytest.mark.unit
def test_session_commit_reports_true_when_the_write_lands():
    session = _session()
    session.add(ub.Bookmark(user_id=1, book_id=2, format="epub", bookmark_key="cfi"))

    assert ub.session_commit(_session=session) is True
    assert session.query(ub.Bookmark).count() == 1


@pytest.mark.unit
@pytest.mark.parametrize("error", [
    sa_exc.OperationalError("stmt", {}, Exception("database is locked")),
    sa_exc.InvalidRequestError("session is in a failed state"),
])
def test_session_commit_reports_false_when_the_write_is_rolled_back(error):
    """THE headline bug: the caller must be able to tell that nothing landed."""
    failing = _FailingSession(error)

    assert ub.session_commit(_session=failing) is False
    assert failing.rolled_back is True, "a swallowed failure must still roll back"


@pytest.mark.unit
def test_session_commit_still_lets_uncaught_errors_propagate():
    """The caught set is load-bearing for ~80 fire-and-forget callers: an
    IntegrityError must keep escaping, or callers that rely on catching it
    (the savepoint in reading_position, for one) silently change behaviour."""
    failing = _FailingSession(sa_exc.IntegrityError("stmt", {}, Exception("UNIQUE constraint failed")))

    with pytest.raises(sa_exc.IntegrityError):
        ub.session_commit(_session=failing)


@pytest.mark.unit
def test_session_flush_reports_whether_the_pending_write_settled():
    session = _session()
    session.add(ub.Bookmark(user_id=1, book_id=2, format="epub", bookmark_key="cfi"))
    assert ub.session_flush(_session=session) is True

    failing = _FailingSession()
    assert ub.session_flush(_session=failing) is False
    assert failing.rolled_back is True


# ── callers that already consume the return value ────────────────────────────

@pytest.mark.unit
def test_password_change_reports_failure_instead_of_exiting_zero(monkeypatch, capsys):
    """`if session_commit() == "":` is unconditionally true today, so the CLI
    tells an admin the password changed and exits 0 even when the write rolled
    back — leaving them locked out and believing otherwise."""
    session = _session()
    user = ub.User()
    user.name = "admin"
    user.email = "admin@example.invalid"
    session.add(user)
    session.commit()

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: False)
    # The password policy is config-driven and irrelevant to what is pinned here:
    # we need execution to reach the commit branch, not to re-test validation.
    monkeypatch.setattr("cps.helper.valid_password", lambda pw: pw)

    with pytest.raises(SystemExit) as exit_info:
        ub.password_change("admin:a-new-password")

    assert exit_info.value.code == 3, "a failed password write must not exit 0"
    assert "Failed changing password" in capsys.readouterr().out


@pytest.mark.unit
def test_password_change_still_reports_success_when_the_write_lands(monkeypatch, capsys):
    session = _session()
    user = ub.User()
    user.name = "admin"
    user.email = "admin@example.invalid"
    session.add(user)
    session.commit()

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: True)
    monkeypatch.setattr("cps.helper.valid_password", lambda pw: pw)

    with pytest.raises(SystemExit) as exit_info:
        ub.password_change("admin:a-new-password")

    assert exit_info.value.code == 0
    assert "changed" in capsys.readouterr().out


# ── the routes ───────────────────────────────────────────────────────────────

def _route_source(module_path, func_name):
    tree = ast.parse((REPO / module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in {module_path}")


def _returns_error_status_on_failed_commit(node):
    """The route must branch on the commit result and return a 5xx, rather than
    falling through to its success status. Read structurally so the pin holds
    across refactors of the surrounding code."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        calls = [c for c in ast.walk(sub.test)
                 if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", getattr(c.func, "id", None)) in
                 ("session_commit", "session_flush")]
        if not calls:
            continue
        for ret in ast.walk(sub):
            if isinstance(ret, ast.Return):
                for const in ast.walk(ret):
                    if isinstance(const, ast.Constant) and const.value in (500, 503):
                        return True
    return False


@pytest.mark.unit
@pytest.mark.parametrize("module_path,func_name", [
    ("cps/web.py", "set_bookmark"),
    ("cps/api/reader.py", "save_bookmark"),
    ("cps/kobo_auth.py", "delete_auth_token"),
    ("cps/admin.py", "edit_domain"),
])
def test_write_routes_answer_an_error_when_the_write_did_not_land(module_path, func_name):
    """A client told 201/204/200 has no reason to retry, so a swallowed failure
    is silent data loss on the reader's hottest path."""
    node = _route_source(module_path, func_name)
    assert _returns_error_status_on_failed_commit(node), (
        f"{func_name} still reports success unconditionally after session_commit"
    )


@pytest.mark.unit
def test_set_bookmark_answers_500_when_the_commit_fails(monkeypatch):
    """Behavioural counterpart to the source pin above, through the real view."""
    from cps import web

    session = _session()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: True)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: False)

    app = _mini_app(web.set_bookmark, "/ajax/bookmark/<int:book_id>/<book_format>")
    with app.test_client() as client:
        with patch.object(web, "current_user", SimpleNamespace(id=7)):
            response = client.post("/ajax/bookmark/42/epub", data={"bookmark": "epubcfi(/6/2)"})

    assert response.status_code == 500


@pytest.mark.unit
def test_save_bookmark_answers_500_when_the_commit_fails(monkeypatch):
    from cps.api import reader as reader_api

    session = _session()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: True)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: False)
    monkeypatch.setattr(reader_api, "_require_real_user", lambda: None)

    app = _mini_app(reader_api.save_bookmark, "/api/v1/books/<int:book_id>/bookmark")
    with app.test_client() as client:
        with patch.object(reader_api, "current_user", SimpleNamespace(id=7)):
            response = client.post("/api/v1/books/42/bookmark",
                                   json={"bookmark": "epubcfi(/6/2)", "format": "epub"})

    assert response.status_code == 500


@pytest.mark.unit
def test_set_bookmark_answers_500_when_the_settling_flush_fails(monkeypatch):
    """The OTHER 500 path, and the one this issue is really about: the bookmark
    write itself failing while being settled. Distinct from a failed final
    commit — a test that only stubs session_commit False would still pass if the
    route ignored session_flush's result entirely."""
    from cps import web

    session = _session()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: False)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: True)
    shared = MagicMock()
    monkeypatch.setattr(web.reading_position, "record_web_reader_progress", shared)

    app = _mini_app(web.set_bookmark, "/ajax/bookmark/<int:book_id>/<book_format>")
    with app.test_client() as client:
        with patch.object(web, "current_user", SimpleNamespace(id=7)):
            response = client.post("/ajax/bookmark/42/epub",
                                   data={"bookmark": "epubcfi(/6/2)", "percentage": "42.5"})

    assert response.status_code == 500
    shared.assert_not_called()  # a bookmark that did not settle must not be shared as progress


@pytest.mark.unit
def test_save_bookmark_answers_500_when_the_settling_flush_fails(monkeypatch):
    from cps.api import reader as reader_api

    session = _session()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: False)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: True)
    monkeypatch.setattr(reader_api, "_require_real_user", lambda: None)
    shared = MagicMock()
    monkeypatch.setattr(reader_api.reading_position, "record_web_reader_progress", shared)

    app = _mini_app(reader_api.save_bookmark, "/api/v1/books/<int:book_id>/bookmark")
    with app.test_client() as client:
        with patch.object(reader_api, "current_user", SimpleNamespace(id=7)):
            response = client.post("/api/v1/books/42/bookmark",
                                   json={"bookmark": "epubcfi(/6/2)", "format": "epub",
                                         "percentage": 42.5})

    assert response.status_code == 500
    shared.assert_not_called()


def _mini_app(view, rule):
    """Mount one view on a bare Flask app: these routes are what we are pinning,
    and a full app init would drag in config/DB/blueprint state the assertion
    does not depend on."""
    import flask

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.add_url_rule(rule, view.__name__, view.__wrapped__ if hasattr(view, "__wrapped__") else view,
                     methods=["POST"])
    return app


# ── the misattribution the settling flush caused ─────────────────────────────

@pytest.mark.unit
def test_progress_helper_does_not_settle_the_callers_write():
    """The settling flush IS the caller's bookmark write. Performed inside the
    helper it lands within the routes' `except Exception: "Could not share web
    reader progress"` guard, so a genuine bookmark failure is logged as an
    optional one and then answered 201. The route that owns the write settles
    it."""
    from cps.services import reading_position

    source = inspect.getsource(reading_position.record_web_reader_progress)
    assert "session.flush()" not in source, (
        "settling the caller's write inside the helper re-opens #1318's "
        "misattribution: the failure surfaces under the routes' progress-sharing guard"
    )


# ── the client half: retrying must not reintroduce a lost position ───────────

@pytest.mark.unit
def test_spa_position_save_is_single_flight():
    """Answering 5xx only helps if the client re-sends — but the route is
    replace-on-write and its CFI carries no ordering or compare-and-set guard,
    so two saves in flight at once can land in the wrong order and move the
    reader BACKWARDS. Retrying without this would make that commonplace: under a
    lock held near SQLite's busy timeout, a reader turning pages would pile up
    dozens of waiting writes, and the oldest could commit last.

    One request at a time, each reading the position refs as it goes out."""
    tsx = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    flush = tsx[tsx.index("const flushCfiSave"):tsx.index("const persistCfi")]

    assert re.search(r"if\s*\(\s*saveInFlight\.current\s*\)\s*\{[^}]*return", flush), \
        "a save starting while one is in flight must coalesce, not race it"
    assert "saveInFlight.current = true" in flush and "saveInFlight.current = false" in flush, \
        "the in-flight flag must be both set and cleared or saving wedges permanently"
    assert ".finally(" in flush, \
        "the flag must clear on failure too, or one failed save stops all saving"
    assert "mutateAsync" in flush, (
        "per-call mutate callbacks only fire for the latest observed mutation and "
        "are dropped on unmount, so the retry bookkeeping would silently stop"
    )


@pytest.mark.unit
def test_spa_announces_a_failing_save_once_not_per_page_turn():
    """The announcer deliberately re-announces identical assertive messages, so
    announcing per failed save would talk over a screen-reader user continuously
    for as long as the condition lasts. Latch on the way in, clear on success."""
    tsx = (REPO / "frontend/src/pages/Reader.tsx").read_text(encoding="utf-8")
    flush = tsx[tsx.index("const flushCfiSave"):tsx.index("const persistCfi")]

    assert re.search(r"if\s*\(\s*!\s*saveFailureAnnounced\.current\s*\)", flush), \
        "the failure announcement must be latched"
    assert "saveFailureAnnounced.current = false" in flush, \
        "the latch must clear on a later success or the reader is told only once, ever"


@pytest.mark.unit
@pytest.mark.parametrize("module_path,func_name", [
    ("cps/web.py", "set_bookmark"),
    ("cps/api/reader.py", "save_bookmark"),
])
def test_bookmark_routes_settle_before_sharing_progress(module_path, func_name):
    """Ordering is the whole point: the required write must settle before the
    savepoint opens, or a rollback inside the optional progress write would take
    the bookmark with it."""
    node = _route_source(module_path, func_name)
    order = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "attr", getattr(sub.func, "id", None))
            if name in ("session_flush", "record_web_reader_progress"):
                order.append((sub.lineno, name))
    order.sort()
    names = [n for _, n in order]
    assert "session_flush" in names, f"{func_name} never settles its own write"
    assert "record_web_reader_progress" in names
    assert names.index("session_flush") < names.index("record_web_reader_progress"), (
        "the bookmark must settle before the progress savepoint opens"
    )
