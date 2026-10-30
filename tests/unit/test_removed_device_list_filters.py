# SPDX-License-Identifier: GPL-3.0-or-later
"""The account can find and restore removed readers after Undo is gone."""

import pytest

from cps import ub
from tests.unit.device_scope_world import DEVICES, ScopeWorld

pytestmark = pytest.mark.unit


def test_device_filters_keep_owner_scope_and_refresh_after_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")
    world = ScopeWorld(monkeypatch, tmp_path)
    try:
        owner = world.add_user("owner")
        other = world.add_user("other")
        active = world.kobo(owner, "Active reader")
        removed = world.kobo(owner, "Removed reader")
        other_removed = world.kobo(other, "Other account's removed reader")
        removed.active = other_removed.active = False
        world.session.commit()
        client = world.web(owner)

        for query, expected in (
            ("", {active.public_id, removed.public_id}),
            ("?active=true", {active.public_id}),
            ("?active=false", {removed.public_id}),
        ):
            response = client.get(DEVICES + query)
            assert response.status_code == 200
            body = response.get_json()
            assert {row["public_id"] for row in body["devices"]} == expected
            assert body["total"] == len(expected)

        # Filtering precedes pagination, including its total count.
        empty_page = client.get(DEVICES + "?active=false&limit=1&offset=1").get_json()
        assert empty_page["devices"] == []
        assert empty_page["total"] == 1

        assert client.post(f"{DEVICES}/{removed.public_id}/restore").status_code == 200
        assert client.get(DEVICES + "?active=false").get_json()["devices"] == []
        restored = client.get(DEVICES + "?active=true").get_json()
        assert {row["public_id"] for row in restored["devices"]} == {
            active.public_id, removed.public_id,
        }
        assert world.session.get(ub.Device, other_removed.id).active is False
    finally:
        world.close()
