# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the admin user-table path of the "sync only selected
shelves to Kobo" transition (fork #1009, follow-up to #866/#1008).

Four places write ``User.kobo_only_shelves_sync``: the classic ``/me`` form
(cps/web.py), the SPA profile endpoint (cps/api/account.py), the admin
single-user form (``_handle_edit_user``), and the admin user *table*
(``edit_list_user``, ``POST /ajax/editlistusers/<param>``). The first three ran
the shelf reconciliation on the 0 -> 1 transition; the table did not, so an
admin who flipped the column got the setting without the shelf tombstones and
the user's device kept collections it should have dropped.

Two further things this pins, both found reviewing the community patch for it:

* The reconciliation must not run unless the setting actually committed.
  ``ub.session_commit()`` swallows ``OperationalError``/``InvalidRequestError``,
  rolls back and returns ``""`` regardless, so "after ``session_commit()``" is
  not the same as "after a successful commit". Writing tombstones for a setting
  that rolled back tells the device to drop collections while the account still
  has the restriction switched off.
* A mid-loop failure must not leave ``ub.session`` dirty. It is not a
  request-scoped session and nothing rolls it back on teardown, so a modified
  user surviving a 400 is flushed by the next commit anywhere in the process.

The transition rule itself now lives in one place
(``kobo_sync_status.needs_shelf_reconciliation``) with one failure policy
(``reconcile_shelves_safely``), because four hand-rolled copies of it are how
the SPA endpoint drifted in the first place.
"""

import inspect
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flask_babel import Babel

from cps import app, ub
import cps.admin as admin
from cps import kobo_sync_status as kss

pytestmark = pytest.mark.unit

# The error path returns a translated string; the bare test app has no babel.
if "babel" not in app.extensions:
    Babel(app)


class _Session:
    """Enough of ``ub.session`` for ``edit_list_user``."""

    def __init__(self, users, commit_error=None):
        self._users = users
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    def query(self, _entity):
        return self

    def filter(self, *_a, **_kw):
        return self

    def all(self):
        return self._users

    def one_or_none(self):
        return self._users[0] if self._users else None

    def commit(self):
        if self.commit_error:
            raise self.commit_error
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _user(uid, enabled=0):
    return types.SimpleNamespace(id=uid, name="u%d" % uid,
                                 kobo_only_shelves_sync=enabled)


def _post(monkeypatch, users, data, commit_error=None):
    """Drive ``POST /ajax/editlistusers/kobo_only_shelves_sync``.

    Returns ``(response, reconciled_user_ids, session)``.
    """
    session = _Session(users, commit_error=commit_error)
    reconciled = []
    monkeypatch.setattr(admin.ub, "session", session)
    monkeypatch.setattr(admin.ub, "session_commit",
                        lambda *a, **kw: pytest.fail(
                            "edit_list_user must use a commit whose failure it can see"))
    monkeypatch.setattr(admin.config, "config_anonbrowse", True, raising=False)
    monkeypatch.setattr(admin.kobo_sync_status, "reconcile_shelves_safely",
                        lambda uid: reconciled.append(uid) or True)
    with app.test_request_context(
        "/ajax/editlistusers/kobo_only_shelves_sync", method="POST", data=data,
    ):
        response = inspect.unwrap(admin.edit_list_user)("kobo_only_shelves_sync")
    return response, reconciled, session


# --- the #1009 defect itself -------------------------------------------------

def test_single_row_toggle_reconciles_1009(monkeypatch):
    """The per-row checkbox posts scalar ``pk`` (table.js ``checkboxChange``) —
    this is the path an admin actually clicks, and it was unreconciled."""
    users = [_user(7, enabled=0)]
    response, reconciled, _ = _post(monkeypatch, users, {"pk": "7", "value": "true"})

    assert response == ""
    assert users[0].kobo_only_shelves_sync == 1
    assert reconciled == [7]


def test_bulk_edit_reconciles_only_the_transitioning_users_1009(monkeypatch):
    """``pk[]`` bulk edit: user 7 goes 0 -> 1 and is reconciled, user 8 is
    already on and must not be swept again."""
    users = [_user(7, enabled=0), _user(8, enabled=1)]
    response, reconciled, _ = _post(monkeypatch, users,
                                    {"pk[]": ["7", "8"], "value": "true"})

    assert response == ""
    assert [u.kobo_only_shelves_sync for u in users] == [1, 1]
    assert reconciled == [7]


def test_turning_the_setting_off_does_not_reconcile_1009(monkeypatch):
    """1 -> 0 widens the sync; there is nothing to tombstone."""
    users = [_user(7, enabled=1)]
    response, reconciled, _ = _post(monkeypatch, users, {"pk": "7", "value": "false"})

    assert response == ""
    assert users[0].kobo_only_shelves_sync == 0
    assert reconciled == []


# --- the commit-gating defect ------------------------------------------------

def test_reconciliation_does_not_run_when_the_commit_fails_1009(monkeypatch):
    """A failed save must not leave the device dropping collections for a
    setting that rolled back."""
    users = [_user(7, enabled=0)]
    response, reconciled, session = _post(
        monkeypatch, users, {"pk": "7", "value": "true"},
        commit_error=RuntimeError("database is locked"))

    assert reconciled == []
    assert session.rollbacks == 1
    assert isinstance(response, tuple) and response[1] == 400


def test_mid_loop_failure_rolls_the_session_back_1009(monkeypatch):
    """``users = [None]`` (a pk that no longer exists) raises inside the loop.
    The half-applied edits must not survive the 400."""
    response, reconciled, session = _post(monkeypatch, [], {"pk": "999", "value": "true"})

    assert reconciled == []
    assert session.rollbacks == 1
    assert session.commits == 0
    assert isinstance(response, tuple) and response[1] == 400


def test_one_users_reconciliation_failure_does_not_strand_the_rest_1009(monkeypatch):
    """``reconcile_shelves_safely`` absorbs per-user failures, so a locked DB on
    user 7 must not skip user 8."""
    users = [_user(7, enabled=0), _user(8, enabled=0)]
    session = _Session(users)
    seen = []

    def flaky(uid):
        seen.append(uid)
        return uid != 7

    monkeypatch.setattr(admin.ub, "session", session)
    monkeypatch.setattr(admin.config, "config_anonbrowse", True, raising=False)
    monkeypatch.setattr(admin.kobo_sync_status, "reconcile_shelves_safely", flaky)
    with app.test_request_context("/ajax/editlistusers/kobo_only_shelves_sync",
                                  method="POST",
                                  data={"pk[]": ["7", "8"], "value": "true"}):
        response = inspect.unwrap(admin.edit_list_user)("kobo_only_shelves_sync")

    assert response == ""
    assert seen == [7, 8]


# --- the shared helpers ------------------------------------------------------

@pytest.mark.parametrize("old,new,expected", [
    (0, 1, True),
    (0, 0, False),
    (1, 1, False),
    (1, 0, False),
    (None, 1, True),
    (False, True, True),
])
def test_needs_shelf_reconciliation_only_fires_off_to_on(old, new, expected):
    assert kss.needs_shelf_reconciliation(old, new) is expected


def test_reconcile_shelves_safely_absorbs_and_reports_failure(monkeypatch):
    """The setting is the user's choice and must stick; the caller gets told
    whether the sweep completed."""
    rolled = []
    monkeypatch.setattr(kss, "update_on_sync_shelfs",
                        lambda uid: (_ for _ in ()).throw(RuntimeError("locked")))
    monkeypatch.setattr(kss, "ub", types.SimpleNamespace(
        session=types.SimpleNamespace(rollback=lambda: rolled.append(1))))

    assert kss.reconcile_shelves_safely(1) is False
    assert rolled == [1]


def test_reconcile_shelves_safely_reports_success(monkeypatch):
    monkeypatch.setattr(kss, "update_on_sync_shelfs", lambda uid: None)
    assert kss.reconcile_shelves_safely(1) is True


# --- the tombstone write itself, on a real engine ----------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _UbProxy:
    def __init__(self, session, counter):
        self.session = session
        self._counter = counter

    def __getattr__(self, name):
        if name == "session_commit":
            def _commit(*a, **kw):
                self._counter.append(1)
                self.session.commit()
            return _commit
        return getattr(ub, name)


def test_bulk_transition_commits_once_per_user_not_once_per_shelf_1009(session, monkeypatch):
    """A bulk enable reaches this once per selected account. On SQLite every
    commit is an fsync, so the per-shelf commit made 100 users x 20 shelves
    2000 serial fsyncs inside one request."""
    for n in range(5):
        session.add(ub.Shelf(name="s%d" % n, user_id=1, kobo_sync=0,
                             uuid="uuid-%d" % n, is_public=0))
    session.commit()
    commits = []
    monkeypatch.setattr(kss, "ub", _UbProxy(session, commits))

    kss.update_on_sync_shelfs(1)

    assert session.query(ub.ShelfArchive).count() == 5
    assert len(commits) == 1, "one commit for the user, not one per shelf"


def test_repeat_transition_adds_no_duplicate_tombstones_and_no_commit_1009(session, monkeypatch):
    """Toggling off and on again must not append a second row per shelf, and
    with nothing to write it must not commit at all."""
    session.add(ub.Shelf(name="s", user_id=1, kobo_sync=0, uuid="uuid-s", is_public=0))
    session.commit()
    commits = []
    monkeypatch.setattr(kss, "ub", _UbProxy(session, commits))

    kss.update_on_sync_shelfs(1)
    kss.update_on_sync_shelfs(1)

    assert session.query(ub.ShelfArchive).count() == 1
    assert len(commits) == 1


def test_kobo_sync_shelves_are_never_tombstoned_1009(session, monkeypatch):
    """The shelves the user marked for Kobo sync are the ones they want kept."""
    session.add(ub.Shelf(name="keep", user_id=1, kobo_sync=1, uuid="uuid-keep", is_public=0))
    session.add(ub.Shelf(name="drop", user_id=1, kobo_sync=0, uuid="uuid-drop", is_public=0))
    session.commit()
    monkeypatch.setattr(kss, "ub", _UbProxy(session, []))

    kss.update_on_sync_shelfs(1)

    assert [r.uuid for r in session.query(ub.ShelfArchive).all()] == ["uuid-drop"]
