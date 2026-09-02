# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint semantics for the server-wide admin "Try My Library" intro card.

Covers the state machine (not_enabled → enabled → dismissed), the pre-enable
snapshot, the true-restore undo (roles + modes, dormant selections), idempotent
re-enable (snapshot never clobbered), and the Guest/anonymous exclusion that
matches the bulk-migration precedent (#2026).
"""
from types import SimpleNamespace
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub

pytestmark = pytest.mark.unit


def _book(book_id, title):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    book = db.Books(title, title, "Author", now, now, "1.0", now,
                    "book-%d" % book_id, 1, [], [])
    book.id = book_id
    return book


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def calibre_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([_book(1, "One"), _book(2, "Two")])
    session.commit()
    yield session
    session.close()


def _user(session, name, *, role=constants.ROLE_DOWNLOAD, own_library=False):
    user = ub.User(name=name, email="%s@example.invalid" % name, password="",
                   has_own_library=own_library, user_library_seeded=own_library,
                   default_language="all")
    user.role = role
    session.add(user)
    session.commit()
    return user


def _cdb(calibre_session):
    instance = object.__new__(db.CalibreDB)
    instance.session = calibre_session
    instance.config = SimpleNamespace(config_restricted_column=0)
    return instance


def _wire(app_session, calibre_session, monkeypatch, administrator):
    """Point the API + user_library seams at the in-memory databases."""
    from cps import user_library
    from cps.api import admin as api_admin
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(user_library, "calibre_db", _cdb(calibre_session))
    monkeypatch.setattr(api_admin, "current_user", administrator)
    return api_admin


def _admin_user(app_session):
    admin = ub.User(name="intro-admin", email="intro-admin@example.invalid",
                    password="", role=constants.ROLE_ADMIN,
                    default_language="all")
    app_session.add(admin)
    app_session.commit()
    return admin


def _call(fn, path, method="GET", body=None):
    app = Flask(__name__)
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
    with app.test_request_context(path, **kwargs):
        return fn.__wrapped__()


# ── state defaults + gating ─────────────────────────────────────────────────

def test_intro_state_defaults_to_not_enabled_without_row(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    response = _call(api_admin.admin_my_library_intro_state,
                     "/api/v1/admin/my-library/intro")
    payload = response.get_json()
    assert payload == {
        "status": "not_enabled", "dismissed": False, "snapshot_accounts": 0,
    }


def test_intro_endpoints_require_admin(app_session, calibre_session,
                                       monkeypatch):
    from cps.api import admin as api_admin
    outsider = SimpleNamespace(is_authenticated=True, is_anonymous=False,
                               role_admin=lambda: False, id=99)
    _wire(app_session, calibre_session, monkeypatch, outsider)
    assert _call(api_admin.admin_my_library_intro_state,
                 "/api/v1/admin/my-library/intro")[1] == 403
    assert _call(api_admin.admin_my_library_intro_enable,
                 "/api/v1/admin/my-library/intro/enable", "POST", {})[1] == 403
    assert _call(api_admin.admin_my_library_intro_undo,
                 "/api/v1/admin/my-library/intro/undo", "POST", {})[1] == 403
    assert _call(api_admin.admin_my_library_intro_dismiss,
                 "/api/v1/admin/my-library/intro/dismiss", "POST", {})[1] == 403


# ── enable: snapshot + grant + switch + Guest exclusion ─────────────────────

def test_enable_snapshots_then_switches_every_non_guest_account(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    reader = _user(app_session, "intro-reader")
    already = _user(app_session, "intro-already",
                    role=constants.ROLE_DOWNLOAD | constants.ROLE_BROWSE_GLOBAL,
                    own_library=True)
    guest = _user(app_session, "Guest", role=constants.ROLE_ANONYMOUS)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)

    response = _call(api_admin.admin_my_library_intro_enable,
                     "/api/v1/admin/my-library/intro/enable", "POST", {})
    payload = response.get_json()

    assert payload["status"] == "enabled"
    assert payload["dismissed"] is False
    assert payload["snapshot_accounts"] == 3  # admin, reader, already — not Guest
    assert payload["errors"] == 0
    assert payload["accounts"] == 3

    # Guest is entirely untouched: no role grant, no seed, no mode switch.
    assert guest.role == constants.ROLE_ANONYMOUS
    assert guest.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert guest.user_library_seeded is False
    assert app_session.query(ub.UserLibraryBook) \
        .filter(ub.UserLibraryBook.user_id == guest.id).count() == 0

    # Everyone else: browse-global granted + personal mode + seeded.
    for user in (admin, reader, already):
        app_session.refresh(user)
        assert user.role_browse_global()
        assert user.library_mode() == constants.LIBRARY_MODE_PERSONAL

    # The snapshot holds the PRE-enable values, including the account that was
    # already personal + browse-global (true-restore needs both directions).
    row = app_session.query(ub.MyLibraryAdminIntro).one()
    import json
    snapshot = json.loads(row.snapshot_json)
    assert snapshot[str(reader.id)] == {
        "browse_global": False, "has_own_library": False,
    }
    assert snapshot[str(already.id)] == {
        "browse_global": True, "has_own_library": True,
    }
    assert str(guest.id) not in snapshot


def test_enable_seeds_each_accounts_full_allowed_catalogue_not_activity(
        app_session, calibre_session, monkeypatch):
    """First setup is a per-account visibility baseline, not prior ownership."""
    english = db.Languages("eng")
    french = db.Languages("fra")
    books = calibre_session.query(db.Books).order_by(db.Books.id).all()
    books[0].languages.append(english)
    books[1].languages.append(english)
    french_book = _book(3, "Three")
    french_book.languages.append(french)
    calibre_session.add(french_book)
    calibre_session.commit()

    admin = _admin_user(app_session)
    english_reader = _user(app_session, "english-reader")
    english_reader.default_language = "eng"
    french_reader = _user(app_session, "french-reader")
    french_reader.default_language = "fra"
    shelf = ub.Shelf(name="Activity", user_id=english_reader.id, is_public=0)
    app_session.add(shelf)
    app_session.commit()
    shelf_link = ub.BookShelf(shelf=shelf.id, book_id=1, order=1)
    shelf_link.ub_shelf = shelf
    app_session.add_all([
        # Activity deliberately names only book 1. Book 2 must still be seeded.
        shelf_link,
        ub.ReadBook(user_id=english_reader.id, book_id=1,
                    read_status=ub.ReadBook.STATUS_FINISHED),
        ub.Downloads(user_id=english_reader.id, book_id=1),
        # Hidden/archived are still part of the baseline so enabling cannot
        # generate accidental device removals.
        ub.UserHiddenBook(user_id=english_reader.id, book_id=2),
        ub.ArchivedBook(user_id=english_reader.id, book_id=2,
                        is_archived=True),
    ])
    app_session.commit()
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)

    response = _call(api_admin.admin_my_library_intro_enable,
                     "/api/v1/admin/my-library/intro/enable", "POST", {})
    assert response.get_json()["errors"] == 0

    memberships = {
        user.id: [row.book_id for row in app_session.query(ub.UserLibraryBook)
                  .filter_by(user_id=user.id)
                  .order_by(ub.UserLibraryBook.book_id)]
        for user in (admin, english_reader, french_reader)
    }
    assert memberships == {
        admin.id: [1, 2, 3],
        english_reader.id: [1, 2],
        french_reader.id: [3],
    }


def test_intro_copy_discloses_full_seed_before_household_action():
    root = Path(__file__).resolve().parents[2]
    component = (root / "frontend/src/components/MyLibraryIntro.tsx").read_text()
    anchors = (root / "cps/spa_strings.py").read_text()
    expected = (
        "Each account starts with every book it can currently see—not only "
        "books it has shelved, read, or downloaded."
    )
    assert expected in component
    assert '_("%s")' % expected.replace('"', '\\"') in anchors


def test_enable_is_idempotent_and_never_re_snapshots(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    reader = _user(app_session, "rerun-reader")
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)

    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    # An administrator flips this account back by hand after the enable.
    reader.role &= ~constants.ROLE_BROWSE_GLOBAL
    reader.has_own_library = False
    app_session.commit()

    response = _call(api_admin.admin_my_library_intro_enable,
                     "/api/v1/admin/my-library/intro/enable", "POST", {})
    payload = response.get_json()
    assert payload["status"] == "enabled"
    assert payload["results"] == []
    # The manual change is NOT absorbed into the snapshot — undo must restore
    # the original pre-enable state, not the mid-flight state.
    row = app_session.query(ub.MyLibraryAdminIntro).one()
    import json
    snapshot = json.loads(row.snapshot_json)
    assert snapshot[str(reader.id)]["browse_global"] is False
    assert snapshot[str(reader.id)]["has_own_library"] is False


# ── undo: true restore, dormant selections, wrong-state guard ────────────────

def test_undo_restores_snapshot_and_leaves_selections_dormant(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    reader = _user(app_session, "undo-reader")
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)

    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    seeded_count = app_session.query(ub.UserLibraryBook) \
        .filter(ub.UserLibraryBook.user_id == reader.id).count()
    assert seeded_count == 2
    assert reader.user_library_seeded is True

    response = _call(api_admin.admin_my_library_intro_undo,
                     "/api/v1/admin/my-library/intro/undo", "POST", {})
    payload = response.get_json()
    assert payload["status"] == "not_enabled"
    assert payload["dismissed"] is False
    assert payload["snapshot_accounts"] == 0
    assert payload["restored_accounts"] == 2  # admin + reader

    app_session.refresh(reader)
    assert not reader.role_browse_global()
    assert reader.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    # Dormant, not deleted: the seed fence and membership rows survive undo, so
    # a later enable restores this exact selection without reseeding.
    assert reader.user_library_seeded is True
    assert app_session.query(ub.UserLibraryBook) \
        .filter(ub.UserLibraryBook.user_id == reader.id).count() == 2

    # Re-enabling after undo takes a FRESH snapshot (post-undo values).
    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    row = app_session.query(ub.MyLibraryAdminIntro).one()
    import json
    snapshot = json.loads(row.snapshot_json)
    assert snapshot[str(reader.id)] == {
        "browse_global": False, "has_own_library": False,
    }


def test_undo_restores_browse_global_in_both_directions(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    # Snapshot said the admin had no browse-global before enable.
    response = _call(api_admin.admin_my_library_intro_undo,
                     "/api/v1/admin/my-library/intro/undo", "POST", {})
    assert response.get_json()["restored_accounts"] == 1
    app_session.refresh(admin)
    assert not admin.role_browse_global()
    assert admin.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY


def test_undo_without_enable_is_a_conflict(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    response = _call(api_admin.admin_my_library_intro_undo,
                     "/api/v1/admin/my-library/intro/undo", "POST", {})
    assert response[1] == 409


def test_undo_skips_accounts_deleted_since_the_snapshot(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    reader = _user(app_session, "doomed-reader")
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    app_session.delete(reader)
    app_session.commit()
    response = _call(api_admin.admin_my_library_intro_undo,
                     "/api/v1/admin/my-library/intro/undo", "POST", {})
    payload = response.get_json()
    assert payload["status"] == "not_enabled"
    assert payload["restored_accounts"] == 1  # only the admin remains


# ── dismiss: enabled-state only, permanent ───────────────────────────────────

def test_dismiss_requires_the_enabled_state(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    response = _call(api_admin.admin_my_library_intro_dismiss,
                     "/api/v1/admin/my-library/intro/dismiss", "POST", {})
    assert response[1] == 409


def test_dismiss_marks_permanent_and_undo_resets_it(
        app_session, calibre_session, monkeypatch):
    admin = _admin_user(app_session)
    api_admin = _wire(app_session, calibre_session, monkeypatch, admin)
    _call(api_admin.admin_my_library_intro_enable,
          "/api/v1/admin/my-library/intro/enable", "POST", {})
    response = _call(api_admin.admin_my_library_intro_dismiss,
                     "/api/v1/admin/my-library/intro/dismiss", "POST", {})
    payload = response.get_json()
    assert payload == {
        "status": "enabled", "dismissed": True, "snapshot_accounts": 1,
    }
    # The dismissal is durable server-side state, not per-browser.
    response = _call(api_admin.admin_my_library_intro_state,
                     "/api/v1/admin/my-library/intro")
    assert response.get_json()["dismissed"] is True

    # Undo returns to not_enabled with the dismissal reset — the card shows
    # again without a close affordance, per the state machine.
    _call(api_admin.admin_my_library_intro_undo,
          "/api/v1/admin/my-library/intro/undo", "POST", {})
    response = _call(api_admin.admin_my_library_intro_state,
                     "/api/v1/admin/my-library/intro")
    assert response.get_json() == {
        "status": "not_enabled", "dismissed": False, "snapshot_accounts": 0,
    }


def test_migration_creates_the_intro_table_for_existing_databases():
    """Upgraded app.db files get the single-row table via the boot migration."""
    engine = create_engine("sqlite:///:memory:")
    # Simulate an upgraded database: every table except the new one.
    tables = [table for table in ub.Base.metadata.sorted_tables
              if table.name != "my_library_admin_intro"]
    ub.Base.metadata.create_all(engine, tables=tables)
    session = sessionmaker(bind=engine)()
    ub.migrate_my_library_admin_intro_table(engine, session)
    from sqlalchemy import inspect as sa_inspect
    assert "my_library_admin_intro" in sa_inspect(engine).get_table_names()
    # Idempotent second run.
    ub.migrate_my_library_admin_intro_table(engine, session)
    session.close()
    engine.dispose()
