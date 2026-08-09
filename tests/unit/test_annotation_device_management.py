# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


def _device(session, *, user_id=7, label="Reader", active=True):
    from cps import ub
    row = ub.Device(user_id=user_id, kind="kobo", display_name=label, active=active,
                    created_by="auto")
    session.add(row)
    session.flush()
    return row


def _annotation(session, annotation_id, *, user_id=7, origin=None, assigned=None):
    from cps import ub
    row = ub.Annotation(user_id=user_id, book_id=5, annotation_id=annotation_id,
                        source="kobo", origin_device_id=origin, assigned_device_id=assigned)
    session.add(row)
    session.flush()
    return row


def test_device_list_aggregates_zero_and_many_annotation_counts(session):
    from cps.annotations import list_annotation_devices
    empty = _device(session, label="Empty")
    busy = _device(session, label="Busy")
    for index in range(12):
        _annotation(session, f"a-{index}", assigned=busy.id)
    rows = {row["public_id"]: row for row in list_annotation_devices(user_id=7, session=session)}
    assert rows[empty.public_id]["annotation_count"] == 0
    assert rows[busy.public_id]["annotation_count"] == 12


@pytest.mark.parametrize("label", ["", "x" * 61, " leading", "trailing ", "bad\nlabel"])
def test_device_rename_rejects_out_of_range_or_unsafe_labels(session, label):
    from cps.annotations import rename_annotation_device
    device = _device(session)
    with pytest.raises(ValueError):
        rename_annotation_device(device.public_id, user_id=7, label=label,
                                 session=session, commit=session.commit)
    assert device.display_name == "Reader"


def test_initial_labels_receive_plain_dedup_suffix(session):
    from cps.services.device_registry import upsert_kobo_device
    common = {"x-kobo-devicemodel": "Kobo Libra Colour"}
    first = upsert_kobo_device(session, user_id=7,
                               headers={**common, "x-kobo-deviceid": "a" * 64}, secret_key="key")
    second = upsert_kobo_device(session, user_id=7,
                                headers={**common, "x-kobo-deviceid": "b" * 64}, secret_key="key")
    assert first.display_name == "Kobo Libra Colour"
    assert second.display_name == "Kobo Libra Colour 2"


def test_soft_delete_preserves_origin_clears_assignment_and_restore_round_trips(session):
    from cps import ub
    from cps.annotations import (device_annotation_counts, restore_annotation_device,
                                 soft_delete_annotation_device)
    device = _device(session, label="Maggie's Libra")
    annotation = _annotation(session, "a-1", origin=device.id, assigned=device.id)
    session.add(ub.AnnotationDeviceState(annotation_id=annotation.id, device_id=device.id,
                                         desired=True, delivery_status="acknowledged"))
    session.commit()
    _, preflight = device_annotation_counts(device.public_id, user_id=7, session=session)
    assert preflight == {"origin_count": 1, "assigned_count": 1}

    deleted, counts = soft_delete_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert counts == preflight
    assert deleted.active is False
    assert deleted.display_name == "Maggie's Libra"
    assert annotation.origin_device_id == device.id
    assert annotation.assigned_device_id is None
    assert session.query(ub.Device).filter_by(id=annotation.origin_device_id).one().display_name == "Maggie's Libra"

    restored, restored_count, conflicts = restore_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert restored.active is True
    assert restored_count == 1 and conflicts == 0
    assert annotation.origin_device_id == device.id
    assert annotation.assigned_device_id == device.id
    state = session.query(ub.AnnotationDeviceState).filter_by(
        annotation_id=annotation.id, device_id=device.id,
    ).one()
    assert state.desired is True and state.delivery_status == "acknowledged"


def test_reassignment_changes_intent_but_never_origin(session):
    from cps import ub
    from cps.annotations import reassign_annotation
    origin = _device(session, label="Origin")
    target = _device(session, label="Target")
    annotation = _annotation(session, "move-me", origin=origin.id, assigned=origin.id)
    session.add(ub.AnnotationDeviceState(annotation_id=annotation.id, device_id=origin.id,
                                         desired=True, delivery_status="acknowledged"))
    session.commit()
    reassign_annotation(
        "move-me", user_id=7, book_id=5, assigned_device_public_id=target.public_id,
        expected_routing_revision=1, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert annotation.origin_device_id == origin.id
    assert annotation.assigned_device_id == target.id
    assert annotation.routing_revision == 2
    states = {state.device_id: state for state in session.query(ub.AnnotationDeviceState).all()}
    assert states[origin.id].desired is False
    assert states[target.id].desired is True
    assert states[target.id].delivery_status == "pending"


def test_bulk_mixed_results_return_200_and_commit_successes(session, monkeypatch):
    from cps import annotations, ub
    target = _device(session, label="Target")
    succeeds = _annotation(session, "succeeds")
    stale = _annotation(session, "stale")
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", session.commit)
    payload = {
        "assigned_device_id": target.public_id,
        "items": [
            {"book_id": 5, "annotation_id": "succeeds", "expected_routing_revision": 1},
            {"book_id": 5, "annotation_id": "stale", "expected_routing_revision": 99},
            {"book_id": 5, "annotation_id": "missing", "expected_routing_revision": 1},
        ],
    }
    with app.test_request_context("/api/annotations/assignments/bulk", method="POST", json=payload):
        response, status = annotations.annotation_assignments_bulk.__wrapped__()
    assert status == 200
    assert response.get_json()["results"] == [
        {"annotation_id": "succeeds", "ok": True},
        {"annotation_id": "stale", "ok": False, "error_code": "revision_conflict"},
        {"annotation_id": "missing", "ok": False, "error_code": "not_found"},
    ]
    session.refresh(succeeds)
    session.refresh(stale)
    assert succeeds.assigned_device_id == target.id
    assert stale.assigned_device_id is None


def test_bulk_rejects_more_than_500_items(session, monkeypatch):
    from cps import annotations, ub
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    with app.test_request_context("/api/annotations/assignments/bulk", method="POST",
                                  json={"assigned_device_id": None, "items": [{}] * 501}):
        response, status = annotations.annotation_assignments_bulk.__wrapped__()
    assert status == 400
    assert response.get_json()["max_items"] == 500


def test_bulk_reports_commit_wrapper_failure_instead_of_false_success(session):
    from cps.annotations import bulk_reassign_annotations
    target = _device(session, label="Target")
    annotation = _annotation(session, "commit-fails")
    session.commit()

    def failed_commit():
        session.rollback()
        return False

    results = bulk_reassign_annotations(
        [{"book_id": 5, "annotation_id": "commit-fails", "expected_routing_revision": 1}],
        user_id=7, assigned_device_public_id=target.public_id,
        session=session, commit=failed_commit,
    )
    assert results == [{"annotation_id": "commit-fails", "ok": False,
                        "error_code": "database_error"}]
    session.refresh(annotation)
    assert annotation.assigned_device_id is None


def test_device_management_migration_twice_and_downgrade_are_reversible():
    from cps import ub
    from sqlalchemy import text
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE annotation (id INTEGER PRIMARY KEY, content_id TEXT)"))
        connection.execute(text("INSERT INTO annotation VALUES (1, 'opaque-history')"))
    ub.migrate_multi_device_annotation_safe_slice(engine, None)
    ub.migrate_device_management_slice(engine, None)
    ub.migrate_device_management_slice(engine, None)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(annotation)"))}
        assert {"origin_device_id", "assigned_device_id", "routing_revision"} <= columns
        assert connection.execute(text("SELECT content_id FROM annotation WHERE id=1")).scalar() == "opaque-history"
    ub.downgrade_device_management_slice(engine)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(annotation)"))}
        tables = {row[0] for row in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))}
        assert not {"origin_device_id", "assigned_device_id", "routing_revision"} & columns
        assert "annotation_device_state" not in tables
        assert "device_retired_assignment" not in tables
        assert connection.execute(text("SELECT content_id FROM annotation WHERE id=1")).scalar() == "opaque-history"
