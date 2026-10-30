# SPDX-License-Identifier: GPL-3.0-or-later
"""#2771: the new UI's email settings could not leave Gmail OAuth, send a test
email, or say whether mail was usable. These cover the API behind that form."""
import inspect
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cps.api import admin as api_admin
from tests.unit import mail_settings_world as world

pytestmark = pytest.mark.unit


@pytest.fixture
def rig(monkeypatch):
    config = world.mail_config()
    monkeypatch.setattr(api_admin, "config", config)
    monkeypatch.setattr(api_admin, "_require_admin", lambda: None)
    return config


def post(json):
    with world.app.test_request_context(json=json):
        return inspect.unwrap(api_admin.admin_update_mail)()


def test_payload_says_mail_is_off_for_gmail_without_a_token(rig):
    with world.app.test_request_context():
        body = inspect.unwrap(api_admin.admin_get_mail)().json
    assert body["mail_configured"] is False
    assert body["gmail_authorised"] is False
    assert body["mail_server_type"] == 1
    assert "stored-secret" not in str(body)


def test_payload_says_mail_is_on_once_gmail_is_authorised(rig):
    rig.mail_gmail_token = {"token": "t"}
    with world.app.test_request_context():
        body = inspect.unwrap(api_admin.admin_get_mail)().json
    assert body["mail_configured"] is True and body["gmail_authorised"] is True


def test_choosing_smtp_switches_off_gmail_and_turns_mail_on(rig):
    response = post({"mail_server": "smtp.gmail.com", "mail_server_type": 0})
    assert response.status_code == 200
    assert rig.mail_server_type == 0
    assert response.json["mail_configured"] is True
    rig.save.assert_called_once()


def test_saving_without_a_type_leaves_a_working_gmail_setup_alone(rig):
    rig.mail_gmail_token = {"token": "t"}
    response = post({"mail_filename_template": "{title}", "mail_server": "left.over.smtp"})
    assert response.status_code == 200
    assert rig.mail_server_type == 1
    assert response.json["mail_configured"] is True


def test_default_smtp_server_is_still_not_configured(rig):
    response = post({"mail_server": "mail.example.org", "mail_server_type": 0})
    assert response.json["mail_configured"] is False


def send_test(monkeypatch, rig, user, sent=None):
    sent = sent if sent is not None else []
    monkeypatch.setattr(api_admin, "current_user", user)
    monkeypatch.setattr(api_admin, "send_test_mail", lambda email, name: sent.append((email, name)))
    with world.app.test_request_context(json={"email": "someone-else@example.invalid"}):
        return inspect.unwrap(api_admin.admin_send_test_mail)(), sent


def test_test_email_goes_only_to_the_admins_own_address(monkeypatch, rig):
    rig.mail_server_type = 0
    (response, status), sent = send_test(monkeypatch, rig, NS(email="admin@example.invalid", name="admin"))
    assert status == 202 and response.json["email"] == "admin@example.invalid"
    assert sent == [("admin@example.invalid", "admin")]


def test_test_email_refuses_while_mail_is_not_configured(monkeypatch, rig):
    (response, status), sent = send_test(monkeypatch, rig, NS(email="admin@example.invalid", name="admin"))
    assert status == 409 and sent == []
    assert response.json["error"]["code"] == "mail_not_configured"


def test_test_email_needs_the_admins_own_address(monkeypatch, rig):
    rig.mail_server_type = 0
    (response, status), sent = send_test(monkeypatch, rig, NS(email="", name="admin"))
    assert status == 409 and sent == []
    assert response.json["error"]["code"] == "email_required"


def test_test_email_is_admin_only(monkeypatch, rig):
    guard = Mock(return_value="forbidden")
    monkeypatch.setattr(api_admin, "_require_admin", guard)
    monkeypatch.setattr(api_admin, "send_test_mail", Mock())
    with world.app.test_request_context():
        assert inspect.unwrap(api_admin.admin_send_test_mail)() == "forbidden"
    api_admin.send_test_mail.assert_not_called()
