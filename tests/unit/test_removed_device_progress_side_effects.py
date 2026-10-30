# SPDX-License-Identifier: GPL-3.0-or-later
"""Refusal precedes book matching and every shared/private progress write."""

import pytest

from cps import ub
from cps.progress_syncing.models import KOSyncProgress
from tests.unit.test_share_place_kosync_gates import (
    DIGEST, XPOINTER, _headers, _register,
    world,  # noqa: F401 - reuse the real HTTP/SQLite fixture
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["legacy", "private", "shared"])
@pytest.mark.parametrize("percentage_only", [False, True])
def test_removed_reader_is_refused_before_matching_or_writing(
        world, monkeypatch, mocker, mode, percentage_only):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0" if mode == "legacy" else "1")
    device = _register(world, "removed-reader", "Removed reader")
    device.active = False
    device.share_place = mode == "shared"
    world.session.commit()
    match = mocker.spy(world.kosync, "enrich_response_with_book_info")
    rollback = mocker.spy(world.session, "rollback")

    body = {
        "document": DIGEST, "percentage": .6,
        "device": "Removed reader", "device_id": "removed-reader",
    }
    if percentage_only:
        body["position_kind"] = "percentage"
    else:
        body["progress"] = XPOINTER
    response = world.client.put(
        "/kosync/syncs/progress", json=body,
        headers=_headers(None if mode == "legacy" else "removed-reader", "Removed reader"),
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "device_inactive",
        "message": "This device was removed from the account, so it no longer syncs",
    }
    rollback.assert_called_once()
    match.assert_not_called()
    for model in (KOSyncProgress, ub.DeviceReadingPosition, ub.ReadBook, ub.KoboReadingState):
        assert world.session.query(model).count() == 0
    assert world.hardcover == []
    assert world.session.get(ub.Device, device.id).active is False
