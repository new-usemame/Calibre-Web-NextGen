# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account-scoped original filename visibility follows the user across clients."""

import pytest


@pytest.mark.unit
def test_classic_checkbox_sentinel_distinguishes_partial_save_from_unchecked():
    from cps.user_preferences import set_checkbox_preference_from_form

    class User:
        is_anonymous = False

        def __init__(self):
            self.view_settings = {"preferences": {"show_original_filename": True}}

        def set_view_property(self, section, prop, value, commit=True):
            self.view_settings.setdefault(section, {})[prop] = value

    user = User()
    assert not set_checkbox_preference_from_form(
        user, {}, "show_original_filename", "show_original_filename_present",
    )
    assert user.view_settings["preferences"]["show_original_filename"] is True
    assert set_checkbox_preference_from_form(
        user, {"show_original_filename_present": "1"},
        "show_original_filename", "show_original_filename_present",
    )
    assert user.view_settings["preferences"]["show_original_filename"] is False


@pytest.mark.unit
def test_original_filename_preference_defaults_on_and_isolated(monkeypatch, tmp_path):
    from cps import ub
    from tests.unit.koreader_library_world import LibraryWorld

    world = LibraryWorld(monkeypatch, tmp_path)
    world.enable_web()
    try:
        alice = world.add_user("alice")
        bob = world.add_user("bob")
        alice_browser = world.browser("alice")
        bob_browser = world.browser("bob")
        assert alice_browser.get("/api/v1/auth/me").get_json()["preferences"]["show_original_filename"] is None
        updated = alice_browser.post(
            "/api/v1/account/preferences", json={
                "preferences": {"show_original_filename": False},
            },
        )
        assert updated.status_code == 200
        assert updated.get_json()["preferences"]["show_original_filename"] is False
        assert alice.view_settings["preferences"]["show_original_filename"] is False
        assert bob.view_settings.get("preferences", {}).get("show_original_filename") is None
        assert bob_browser.get("/api/v1/auth/me").get_json()["preferences"]["show_original_filename"] is None
    finally:
        world.close()


@pytest.mark.unit
def test_anonymous_cannot_change_guest_filename_preference(monkeypatch, tmp_path):
    from cps import ub
    from tests.unit.koreader_library_world import LibraryWorld

    world = LibraryWorld(monkeypatch, tmp_path)
    world.enable_web()
    try:
        guest = world.session.query(ub.User).filter_by(name="Guest").one()
        response = world.browser().post(
            "/api/v1/account/preferences", json={
                "preferences": {"show_original_filename": False},
            },
        )
        assert response.status_code == 401
        assert guest.get_view_property("preferences", "show_original_filename") is None
    finally:
        world.close()


@pytest.mark.unit
def test_profile_partial_save_preserves_original_filename_preference(monkeypatch, tmp_path):
    from tests.unit.koreader_library_world import LibraryWorld

    world = LibraryWorld(monkeypatch, tmp_path)
    world.enable_web()
    try:
        user = world.add_user("alice")
        user.set_view_property("preferences", "show_original_filename", False)
        world.session.commit()
        response = world.browser("alice").post(
            "/api/v1/account/profile", json={"theme": "dark"},
        )
        assert response.status_code == 200
        assert user.view_settings["preferences"]["show_original_filename"] is False
    finally:
        world.close()


@pytest.mark.unit
def test_classic_book_detail_obeys_account_filename_preference(monkeypatch, tmp_path):
    from tests.fixtures.detail_template import render_detail

    filename = "very-long-source-name.epub"
    assert filename in render_detail(original_filename=filename)
    assert filename in render_detail(original_filename=filename, show_original_filename=True)
    assert filename not in render_detail(original_filename=filename, show_original_filename=False)
