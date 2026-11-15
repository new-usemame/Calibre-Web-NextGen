# SPDX-License-Identifier: GPL-3.0-or-later
"""The real Kobo blueprint behind ``requires_kobo_auth`` with one user and one
Kobo sync token (``reader-token``), store proxying off. Shared app for tests of
the Kobo store-API routes a device calls while it is being paired (D#2781).
"""

from types import SimpleNamespace

import pytest
from flask import Flask, make_response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HEADERS = {"x-kobo-deviceid": "b" * 64, "x-kobo-devicemodel": "Kobo Libra Colour"}


@pytest.fixture
def kobo_app(tmp_path, monkeypatch):
    from cps import constants, kobo, kobo_auth, ub
    from cps.cw_login import LoginManager
    from cps.usermanagement import load_user

    engine = create_engine(f"sqlite:///{tmp_path / 'app.sqlite'}")
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(kobo_auth, "limiter", SimpleNamespace(check=lambda: None, current_limits=[]))
    monkeypatch.setattr(kobo.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_allow_reverse_proxy_header_login", False, raising=False)

    user = ub.User(name="reader", email="reader@example.invalid", password="unused", role=0)
    database.add(user)
    database.flush()
    token = ub.RemoteAuthToken()
    token.user_id = user.id
    token.auth_token = "reader-token"
    token.token_type = 1
    database.add(token)
    database.commit()

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-session-secret")
    LoginManager(app).user_loader(load_user)
    app.register_blueprint(kobo.kobo)
    yield SimpleNamespace(app=app, kobo=kobo)
    database.close()
    engine.dispose()
