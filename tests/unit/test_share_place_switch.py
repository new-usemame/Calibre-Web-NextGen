# SPDX-License-Identifier: GPL-3.0-or-later
"""Malformed rollout selectors fail closed, and changes are read immediately."""

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", [None, "0", "true", "user:", "user:3,nope",
                                  "user:3,", "user:3.0", "user:3,,107"])
def test_invalid_selector_is_off(monkeypatch, value):
    from cps.services.share_place import gates_enforced
    if value is None:
        monkeypatch.delenv("CWNG_SHARE_PLACE_GATES", raising=False)
    else:
        monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", value)
    assert gates_enforced(3) is False


def test_selector_is_per_account_and_not_cached(monkeypatch):
    from cps.services.share_place import gates_enforced
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "user:3,107")
    assert gates_enforced(3) is True
    assert gates_enforced(107) is True
    assert gates_enforced(4) is False
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")
    assert gates_enforced(4) is True
    monkeypatch.delenv("CWNG_SHARE_PLACE_GATES")
    assert gates_enforced(3) is False
