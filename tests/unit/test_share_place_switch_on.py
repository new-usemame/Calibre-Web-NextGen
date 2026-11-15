# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place PR 5: the rollout switch flips to on.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 17
("The switch flips"). ``CWNG_SHARE_PLACE_GATES``:

* unset: on, for every account;
* ``0``: off, the documented emergency off switch;
* ``user:N[,M...]``: on only for those accounts;
* ``1``: on for every account;
* anything else is malformed: off, with a logged warning, so a typo fails
  toward today's behaviour and never silently on.
"""

import logging

import pytest

from tests.unit.test_share_place_kosync_gates import (  # noqa: F401 - fixture
    SWITCH,
    _assert_gated_for_an_identified_unchecked_device,
    _auth,
    world,
)

pytestmark = pytest.mark.unit

MALFORMED = ["true", "yes", "on", "2", "users:3", "user:", "user:3,nope", "user:3,",
             "user:3.0", "user:3,,107"]


def _enforced(monkeypatch, value, user_id=3):
    from cps.services.share_place import gates_enforced
    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value)
    return gates_enforced(user_id)


def test_unset_is_on_for_every_account(monkeypatch):
    assert _enforced(monkeypatch, None, 3) is True
    assert _enforced(monkeypatch, None, 4) is True


def test_zero_is_the_emergency_off_switch(monkeypatch):
    assert _enforced(monkeypatch, "0", 3) is False


def test_one_is_on_for_every_account(monkeypatch):
    assert _enforced(monkeypatch, "1", 3) is True
    assert _enforced(monkeypatch, "1", 4) is True


def test_an_allow_list_scopes_enforcement_to_its_accounts(monkeypatch):
    assert _enforced(monkeypatch, "user:3,107", 3) is True
    assert _enforced(monkeypatch, "user:3,107", 107) is True
    assert _enforced(monkeypatch, "user:3,107", 4) is False


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_value_is_off_and_says_so(monkeypatch, caplog, value):
    with caplog.at_level(logging.WARNING):
        assert _enforced(monkeypatch, value, 3) is False
    warned = [record for record in caplog.records
              if record.levelno >= logging.WARNING and SWITCH in record.getMessage()]
    assert warned, "a malformed %s must be logged" % SWITCH


def test_a_valid_value_logs_no_warning(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        for value in (None, "0", "1", "user:3,107"):
            _enforced(monkeypatch, value, 3)
    assert not [record for record in caplog.records if SWITCH in record.getMessage()]


def test_the_switch_is_read_on_every_call(monkeypatch):
    assert _enforced(monkeypatch, "0", 3) is False
    assert _enforced(monkeypatch, None, 3) is True
    assert _enforced(monkeypatch, "user:4", 3) is False
    assert _enforced(monkeypatch, None, 3) is True


# -- end to end through KOSync, with nothing set --------------------------------

def test_with_nothing_set_an_unchecked_identified_device_keeps_its_place_to_itself(
        world, monkeypatch):
    """A fresh install: no variable, every device starts unchecked (section 3.1),
    so an identified Kindle's reading moves only its own place."""
    monkeypatch.delenv(SWITCH, raising=False)

    _assert_gated_for_an_identified_unchecked_device(world)


def test_with_nothing_set_the_auth_reply_tells_the_plugin_gates_are_enforced(
        world, monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)

    assert _auth(world)["share_place_gates"] is True
