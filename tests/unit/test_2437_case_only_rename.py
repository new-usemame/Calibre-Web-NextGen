# SPDX-License-Identifier: GPL-3.0-or-later
"""#2437: an account can be renamed to a different letter case of its own name.

Usernames and email addresses are unique case-insensitively, and the check used
to count the account being edited as the clash, so "myname" -> "MyName" was
always "already taken". The classic admin form then re-rendered the page
without the shelf lists the template needs, turning that message into a 500.
"""
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import constants, helper, ub


@pytest.fixture
def users(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    admin = ub.User(name="myname", email="me@example.invalid",
                    role=constants.ROLE_ADMIN, password="x")
    other = ub.User(name="reader", email="reader@example.invalid",
                    role=constants.ROLE_USER, password="x")
    session.add_all([admin, other])
    session.commit()
    yield session, admin, other
    session.close()


@pytest.mark.unit
def test_an_account_can_take_a_different_case_of_its_own_name(users):
    _, admin, _ = users
    assert helper.check_username("MyName", admin.id) == "MyName"
    assert helper.check_email("Me@Example.invalid", admin.id) == "Me@Example.invalid"


@pytest.mark.unit
def test_another_accounts_name_is_still_taken_in_any_case(users):
    _, admin, _ = users
    with pytest.raises(Exception, match="already taken"):
        helper.check_username("READER", admin.id)
    with pytest.raises(Exception, match="existing account"):
        helper.check_email("READER@example.invalid", admin.id)


@pytest.mark.unit
def test_creating_an_account_still_rejects_every_existing_name(users):
    with pytest.raises(Exception, match="already taken"):
        helper.check_username("MYNAME")
    with pytest.raises(Exception, match="existing account"):
        helper.check_email("me@example.invalid")
    assert helper.check_username("  newbie  ") == "newbie"


@pytest.mark.unit
def test_admin_form_renames_to_a_case_variant_and_saves(users, monkeypatch):
    session, admin, _ = users
    calls = _drive_edit_user(monkeypatch, admin.id, {"name": "MyName"})
    session.expire_all()
    assert session.get(ub.User, admin.id).name == "MyName"
    assert ("error" not in [c for _, c in calls["flash"]]), calls["flash"]


@pytest.mark.unit
def test_rejected_admin_form_shows_the_message_on_a_complete_page(users, monkeypatch):
    session, admin, _ = users
    calls = _drive_edit_user(monkeypatch, admin.id,
                             {"name": "Reader", "email": "new@example.invalid"})
    assert ("This username is already taken", "error") in calls["flash"]
    (context,) = calls["render"]
    # user_edit.html concatenates these two lists for any non-anonymous account.
    assert isinstance(context["visible_public_shelves"], list)
    assert isinstance(context["hidden_custom_shelves"], list)
    session.expire_all()
    stored = session.get(ub.User, admin.id)
    assert stored.name == "myname"
    assert stored.email == "me@example.invalid", "a rejected form must not save its other fields"


class _UnsetConfig:
    """An instance with every setting left at its empty default."""

    def __getattr__(self, name):
        if name.startswith("config_"):
            return None
        return lambda *_a, **_k: False


def _drive_edit_user(monkeypatch, user_id, changes):
    import cps.admin as admin_module
    from cps import app

    calls = {"flash": [], "render": []}
    monkeypatch.setattr(admin_module, "config", _UnsetConfig())
    untranslated = lambda msg, **kw: msg % kw if kw else msg  # noqa: E731
    monkeypatch.setattr(admin_module, "_", untranslated)
    monkeypatch.setattr(helper, "_", untranslated)
    monkeypatch.setattr(admin_module, "get_sidebar_config", lambda: ([], None))
    monkeypatch.setattr(admin_module, "flag_modified", lambda *_a, **_k: None)
    monkeypatch.setattr(admin_module, "flash",
                        lambda msg, category="message": calls["flash"].append((str(msg), category)))
    monkeypatch.setattr(admin_module, "render_title_template",
                        lambda _tpl, **ctx: calls["render"].append(ctx) or "page")
    monkeypatch.setattr(admin_module.calibre_db, "speaking_language", lambda **_k: [])
    monkeypatch.setattr(admin_module, "get_available_locale", lambda: [])
    monkeypatch.setattr(admin_module, "get_custom_column_visibility_options", lambda *_a: [])
    monkeypatch.setattr(admin_module, "save_cc_visibility", lambda *_a, **_k: None)
    monkeypatch.setattr(admin_module, "_build_opds_context", lambda _c: {
        "opds_root_order_string": "", "opds_hidden_entries_string": "", "opds_root_labels": {}})
    monkeypatch.setattr(admin_module, "_build_magic_shelf_order_context", lambda _c: {
        "magic_shelf_order_string": "", "magic_shelf_order_labels": {},
        "magic_shelf_order_mode": ""})

    form = {"email": "me@example.invalid", "kindle_mail": "", "admin_role": "on",
            "kindle_mail_subject": ""}
    form.update(changes)
    handler = inspect.unwrap(admin_module.edit_user)
    with app.test_request_context(f"/admin/user/{user_id}", method="POST", data=form):
        assert handler(user_id) == "page"
    return calls
