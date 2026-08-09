# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
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
    assert state.desired is True and state.delivery_status == "pending"
