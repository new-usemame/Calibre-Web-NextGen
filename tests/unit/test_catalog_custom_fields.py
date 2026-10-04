# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader custom-field choices persist atomically and stay inside the admin allowlist."""
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


@pytest.fixture()
def preferences(monkeypatch, tmp_path):
    from cps import ub
    from cps.api import account
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    ub.User.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([ub.User(id=1, name="one", email="one@example.test", view_settings={}),
                     ub.User(id=2, name="two", email="two@example.test", view_settings={})])
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(account, "load_configured_columns", lambda _config, **_kwargs: [SimpleNamespace(id=12)])
    monkeypatch.setattr(account, "current_user", session.get(ub.User, 1))
    app = flask.Flask(__name__)

    def post(body):
        with app.test_request_context("/api/v1/account/catalog-custom-fields", method="POST", json=body):
            result = account.update_catalog_custom_fields()
            if isinstance(result, tuple):
                return result[0].get_json(), result[1]
            return result.get_json(), result.status_code
    try:
        yield account, session, post
    finally:
        session.close()
        engine.dispose()


def test_custom_fields_save_survives_reload_without_changing_another_reader(preferences):
    from cps import ub
    _account, session, post = preferences
    body, status = post({"custom_column_ids": [12], "custom_column_labels": {"12": "  Difficulty  "}})
    assert status == 200 and body == {"custom_field_ids": [12], "custom_field_labels": {"12": "Difficulty"}}
    session.expire_all()
    assert session.get(ub.User, 1).view_settings["catalog"] == body
    assert session.get(ub.User, 2).view_settings == {}
    body, status = post({"custom_column_ids": [], "custom_column_labels": {}})
    session.expire_all()
    assert status == 200 and session.get(ub.User, 1).view_settings["catalog"] == body


@pytest.mark.parametrize("body", [
    [12], {"custom_column_ids": [True]}, {"custom_column_ids": [999]},
    {"custom_column_ids": [12], "custom_column_labels": {"012": "Alias"}},
    {"custom_column_ids": [12], "custom_column_labels": {"12": "x" * 81}},
])
def test_invalid_custom_fields_do_not_write_preferences(preferences, body):
    from cps import ub
    _account, session, post = preferences
    _result, status = post(body)
    assert status == 400
    session.expire_all()
    assert session.get(ub.User, 1).view_settings == {}


def test_custom_fields_commit_failure_rolls_back_both_choices(preferences, monkeypatch):
    from cps import ub
    _account, session, post = preferences
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(
        OperationalError("UPDATE user", {}, RuntimeError("write failed"))))
    _body, status = post({"custom_column_ids": [12], "custom_column_labels": {"12": "Difficulty"}})
    assert status == 500
    session.expire_all()
    assert session.get(ub.User, 1).view_settings == {}


def test_guest_cannot_save_and_unavailable_columns_preserve_choices(preferences, monkeypatch):
    account, session, post = preferences
    monkeypatch.setattr(account, "load_configured_columns", lambda _config, **_kwargs: None)
    _body, status = post({"custom_column_ids": [12]})
    assert status == 503 and account.current_user.view_settings == {}
    monkeypatch.setattr(account, "current_user", SimpleNamespace(is_authenticated=True, is_anonymous=True))
    _body, status = post({"custom_column_ids": [12]})
    assert status == 401
