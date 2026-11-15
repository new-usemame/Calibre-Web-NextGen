# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1734 — own shelf sharing is a separate, default-on account capability."""
from sqlalchemy import create_engine, text
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_upgrade_adds_default_on_sharing_column_and_can_run_twice():
    from cps import ub

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO user (id, name) VALUES (1, 'legacy')"))

    assert ub.migrate_user_share_shelfs(engine) is True
    assert ub.migrate_user_share_shelfs(engine) is False
    with engine.connect() as connection:
        upgraded = connection.execute(
            text("SELECT share_shelfs FROM user WHERE id = 1")
        ).scalar_one()
    assert upgraded == 1


@pytest.mark.unit
def test_share_capability_is_separate_from_edit_public_shelves():
    from cps import ub

    user = type("UserCapability", (), {
        "is_authenticated": True,
        "is_anonymous": False,
        "share_shelfs": True,
    })()
    assert ub.UserBase.role_share_shelfs(user) is True

    user.share_shelfs = False
    assert ub.UserBase.role_share_shelfs(user) is False

    user.is_anonymous = True
    user.share_shelfs = True
    assert ub.UserBase.role_share_shelfs(user) is False


@pytest.mark.unit
def test_public_shelf_owner_can_edit_when_sharing_is_separate_from_editor_role():
    from cps import shelf as shelf_module

    shelf = SimpleNamespace(id=8, name="Owner's public shelf", is_public=1, user_id=12)
    owner = SimpleNamespace(id=12, role_edit_shelfs=lambda: False)
    with patch.object(shelf_module, "current_user", owner):
        assert shelf_module.check_shelf_edit_permissions(shelf) is True

    editor = SimpleNamespace(id=99, role_edit_shelfs=lambda: True)
    with patch.object(shelf_module, "current_user", editor):
        assert shelf_module.check_shelf_edit_permissions(shelf) is True

    viewer = SimpleNamespace(id=99, role_edit_shelfs=lambda: False)
    with patch.object(shelf_module, "current_user", viewer):
        assert shelf_module.check_shelf_edit_permissions(shelf) is False


@pytest.mark.unit
def test_anonymous_guest_cannot_edit_a_public_shelf_owned_by_guest_account():
    from cps import shelf as shelf_module

    shelf = SimpleNamespace(id=8, name="Guest shelf", is_public=1, user_id=12)
    guest = SimpleNamespace(id=12, is_anonymous=True, role_edit_shelfs=lambda: True)
    with patch.object(shelf_module, "current_user", guest):
        assert shelf_module.check_shelf_edit_permissions(shelf) is False
