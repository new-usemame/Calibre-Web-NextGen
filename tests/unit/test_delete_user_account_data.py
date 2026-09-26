# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deleting a user removes everything app.db holds for that account.

OBSERVED on a production server: the admin "delete user" route answered
"User deleted", yet app.db still held the account's 9 registered devices,
2 KOReader reading positions and 5 magic shelves. The per-user-book rows went
through one enumerator; the rest was a hand-written list that had fallen
behind the schema.

Two tests: the reported scenario through the real ``_delete_user``, and a
registry walk that seeds a row in EVERY table reachable from ``user`` by a
foreign key (or a ``user_id`` column) for two users, deletes one, and checks
that none of the deleted user's rows survive while the other user's all do.
The walk is what keeps the next user-scoped table from being forgotten.
"""

import itertools
import os
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit


# Tables that may keep a deleted user's rows, with the reason. Empty today:
# everything reachable from ``user`` is the account's own data.
EXEMPT_TABLES = {}


@pytest.fixture
def ub_module():
    import sys
    # some suites stub cps.*, flask, sqlalchemy… into sys.modules and don't
    # restore — evict the whole affected families so we import the real ones.
    if "cps.ub" in sys.modules and not hasattr(sys.modules["cps.ub"], "Base"):
        stubbed = {"cps", "cwa_db", "flask", "flask_babel", "flask_dance",
                   "sqlalchemy", "werkzeug"}
        for name in [m for m in list(sys.modules) if m.split(".")[0] in stubbed]:
            sys.modules.pop(name, None)
    from cps import ub
    # KOSyncProgress lives on the app.db Base in its own module.
    import cps.progress_syncing.models  # noqa: F401
    return ub


@pytest.fixture
def session(ub_module):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub_module.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.close()


def _user(ub, session, name, role=None):
    from cps import constants
    user = ub.User(name=name, email="%s@example.invalid" % name, password="",
                   role=constants.ROLE_USER if role is None else role,
                   default_language="all")
    session.add(user)
    session.flush()
    return user


# ---------------------------------------------------------------------------
# Generic row seeding over the model registry
# ---------------------------------------------------------------------------

_counter = itertools.count(1)

# NOT NULL columns without a default whose CHECK constraints a dummy value
# would violate.
_OVERRIDES = {
    "notice_event": {"scope": "global", "book_id": None},
    "device_storage_snapshot": {"total_bytes": 10, "free_bytes": 5},
    "kobo_annotation_seed_capture": {"result": "pending",
                                     "seed_kind": "routing_only"},
    "kobo_annotation_materialization": {"provenance": "cwng_authored",
                                        "attachments_state": "empty"},
}


def _dummy(column):
    n = next(_counter)
    t = column.type
    enums = getattr(t, "enums", None)
    if enums:
        return enums[0]
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.Numeric)):
        return float(n) if isinstance(t, sa.Float) else n
    if isinstance(t, sa.DateTime):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    if isinstance(t, sa.Date):
        return date(2026, 1, 1)
    if isinstance(t, sa.JSON):
        return {}
    if isinstance(t, sa.LargeBinary):
        return b"x%d" % n
    return "x%d" % n


def _user_scoped_tables(ub):
    """Every app.db table reachable from ``user``: a ``user_id`` column, or a
    foreign key into another user-scoped table (Device, Shelf, Annotation…)."""
    tables = ub.Base.metadata.sorted_tables
    scoped = {"user"}
    changed = True
    while changed:
        changed = False
        for table in tables:
            if table.name in scoped:
                continue
            if "user_id" in table.c or any(
                    fk.column.table.name in scoped for fk in table.foreign_keys):
                scoped.add(table.name)
                changed = True
    scoped.discard("user")
    return scoped


def _seed(session, table, owner_rows, shared_rows, scoped):
    """Insert one row into ``table`` whose every foreign key points at this
    owner's row of the referenced table (or a shared row outside the user
    scope). Returns the inserted values."""
    values = dict(_OVERRIDES.get(table.name, {}))
    for column in table.columns:
        if column.name in values:
            continue
        fks = list(column.foreign_keys)
        if fks:
            target = fks[0].column
            if target.table.name in scoped or target.table.name == "user":
                parent = owner_rows[target.table.name]
            else:
                parent = shared_rows.get(target.table.name)
                if parent is None:
                    parent = _seed(session, target.table, owner_rows,
                                   shared_rows, scoped)
                    shared_rows[target.table.name] = parent
            values[column.name] = parent[target.name]
        elif column.primary_key and isinstance(column.type, sa.Integer) \
                and len(table.primary_key.columns) == 1:
            continue  # autoincrement
        elif column.nullable or column.default is not None \
                or column.server_default is not None:
            continue
        else:
            values[column.name] = _dummy(column)
    result = session.execute(table.insert().values(**values))
    pk = result.inserted_primary_key
    for column, value in zip(table.primary_key.columns, pk):
        values[column.name] = value
    return values


def _owned_rows_left(session, table, seeded):
    pk = list(table.primary_key.columns)
    clause = sa.and_(*[c == seeded[c.name] for c in pk])
    return session.execute(
        sa.select(sa.func.count()).select_from(table).where(clause)).scalar()


def test_every_user_scoped_table_is_purged_with_the_account(session, ub_module,
                                                            monkeypatch,
                                                            tmp_path):
    from cps import admin, constants
    ub = ub_module
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))

    scoped = _user_scoped_tables(ub)
    # Sanity: the walk sees the tables this bug was about.
    assert {"device", "device_identity", "kosync_progress", "magic_shelf",
            "magic_shelf_cache", "annotation", "book_shelf_link"} <= scoped
    assert len(scoped) > 50

    _user(ub, session, "admin", role=constants.ROLE_ADMIN)
    victim = _user(ub, session, "victim")
    survivor = _user(ub, session, "survivor")
    shared = {}
    seeded = {}
    for owner in (victim, survivor):
        rows = {"user": {"id": owner.id}}
        for table in ub.Base.metadata.sorted_tables:
            if table.name in scoped:
                rows[table.name] = _seed(session, table, rows, shared, scoped)
        seeded[owner.id] = rows
    session.commit()
    victim_id, survivor_id = victim.id, survivor.id

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit",
                        lambda *_a, **_kw: session.commit())
    admin._delete_user(victim)

    tables = {t.name: t for t in ub.Base.metadata.sorted_tables}
    leftovers = sorted(
        name for name in scoped - set(EXEMPT_TABLES)
        if _owned_rows_left(session, tables[name], seeded[victim_id][name]))
    assert leftovers == [], (
        "deleting a user left rows in: %s — add the table to "
        "cps/user_account_data.py (or user_book_data.py), or to "
        "EXEMPT_TABLES with a reason" % leftovers)

    lost = sorted(
        name for name in scoped
        if not _owned_rows_left(session, tables[name],
                                seeded[survivor_id][name]))
    assert lost == [], "deleting one user removed another's rows in %s" % lost
    assert session.get(ub.User, victim_id) is None
    assert session.get(ub.User, survivor_id) is not None


# ---------------------------------------------------------------------------
# The reported scenario, through the real admin delete
# ---------------------------------------------------------------------------

def _device_with_children(ub, session, user, name):
    device = ub.Device(user_id=user.id, kind="kobo", display_name=name)
    session.add(device)
    session.flush()
    report = ub.DeviceInventoryReport(device_id=device.id, **_required(
        ub.DeviceInventoryReport, exclude={"device_id"}))
    session.add(report)
    session.flush()
    session.add_all([
        ub.DeviceIdentity(device_id=device.id, **_required(
            ub.DeviceIdentity, exclude={"device_id"})),
        ub.DeviceInventoryItem(device_id=device.id, last_report_id=report.id,
                               **_required(ub.DeviceInventoryItem,
                                           exclude={"device_id",
                                                    "last_report_id"})),
        ub.DeviceStorageSnapshot(device_id=device.id, total_bytes=10,
                                 free_bytes=5, **_required(
                                     ub.DeviceStorageSnapshot,
                                     exclude={"device_id", "total_bytes",
                                              "free_bytes"})),
        ub.DeviceReadingPosition(device_id=device.id, book_id=1,
                                 progress_percent=40.0),
    ])
    return device


def _required(model, exclude=()):
    """Dummy values for the NOT NULL, default-less columns of ``model``."""
    values = {}
    for column in model.__table__.columns:
        if column.name in exclude or column.foreign_keys or column.nullable \
                or column.default is not None \
                or column.server_default is not None:
            continue
        if column.primary_key and isinstance(column.type, sa.Integer):
            continue
        values[column.name] = _dummy(column)
    return values


def _populate(ub, session, user, *, public_magic=False):
    from cps.progress_syncing.models import KOSyncProgress
    from cps.services import user_cover
    _device_with_children(ub, session, user, "%s's Kobo" % user.name)
    _device_with_children(ub, session, user, "%s's browser" % user.name)
    session.add(KOSyncProgress(user_id=user.id, document="doc-" + user.name,
                               progress="/body/p[3]", percentage=0.4,
                               device="KOReader", device_id="dev-1"))
    magic = ub.MagicShelf(user_id=user.id, name="Unread", is_public=int(public_magic),
                          rules={"rules": []})
    shelf = ub.Shelf(user_id=user.id, name="Shelf", uuid="shelf-" + user.name)
    session.add_all([magic, shelf])
    session.flush()
    session.add(ub.MagicShelfCache(shelf_id=magic.id, user_id=user.id,
                                   book_ids=[1, 2], total_count=2))
    session.add(ub.UserBookCover(user_id=user.id, book_id=1))
    os.makedirs(user_cover.cover_directory(user.id), exist_ok=True)
    with open(user_cover.cover_path(user.id, 1), "wb") as f:
        f.write(b"jpeg")
    return magic


def _rows_for(ub, session, user_id):
    from cps.progress_syncing.models import KOSyncProgress
    device_ids = [d.id for d in session.query(ub.Device.id).filter(
        ub.Device.user_id == user_id)]
    counts = {
        "device": len(device_ids),
        "kosync_progress": session.query(KOSyncProgress).filter_by(
            user_id=user_id).count(),
        "magic_shelf": session.query(ub.MagicShelf).filter_by(
            user_id=user_id).count(),
        "magic_shelf_cache": session.query(ub.MagicShelfCache).filter_by(
            user_id=user_id).count(),
        "hidden_magic_shelf_templates": session.query(
            ub.HiddenMagicShelfTemplate).filter_by(user_id=user_id).count(),
        "shelf": session.query(ub.Shelf).filter_by(user_id=user_id).count(),
        "user_book_cover": session.query(ub.UserBookCover).filter_by(
            user_id=user_id).count(),
    }
    for model in (ub.DeviceIdentity, ub.DeviceInventoryReport,
                  ub.DeviceInventoryItem, ub.DeviceStorageSnapshot,
                  ub.DeviceReadingPosition):
        counts[model.__tablename__] = session.query(model).filter(
            model.device_id.in_(device_ids or [-1])).count()
    return counts


def test_admin_delete_user_removes_devices_kosync_and_magic_shelves(
        session, ub_module, monkeypatch, tmp_path):
    from cps import admin, constants
    from cps.services import user_cover
    ub = ub_module
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))

    _user(ub, session, "admin", role=constants.ROLE_ADMIN)
    victim = _user(ub, session, "kindle-test")
    survivor = _user(ub, session, "reader")
    victim_magic = _populate(ub, session, victim, public_magic=True)
    _populate(ub, session, survivor)
    # The survivor has seen and hidden the victim's public magic shelf.
    session.add_all([
        ub.MagicShelfCache(shelf_id=victim_magic.id, user_id=survivor.id,
                           book_ids=[1], total_count=1),
        ub.HiddenMagicShelfTemplate(user_id=survivor.id,
                                    shelf_id=victim_magic.id),
    ])
    session.commit()
    victim_id, survivor_id = victim.id, survivor.id
    survivor_before = _rows_for(ub, session, survivor_id)

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit",
                        lambda *_a, **_kw: session.commit())
    message = admin._delete_user(victim)
    assert "kindle-test" in str(message)

    left = {table: n for table, n in _rows_for(ub, session, victim_id).items()
            if n}
    assert left == {}
    assert session.query(ub.User).filter_by(id=victim_id).count() == 0
    assert not os.path.exists(user_cover.cover_directory(victim_id))

    # The survivor keeps everything of their own; only their view of the
    # deleted user's public magic shelf (cache + hide record) goes with it.
    survivor_after = _rows_for(ub, session, survivor_id)
    assert survivor_before["magic_shelf_cache"] == 2
    assert survivor_before["hidden_magic_shelf_templates"] == 1
    assert survivor_after == dict(survivor_before, magic_shelf_cache=1,
                                  hidden_magic_shelf_templates=0)
    assert os.path.exists(user_cover.cover_path(survivor_id, 1))
