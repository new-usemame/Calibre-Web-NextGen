import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

import cps.logout
from cps import constants, ub
from cps.MyLoginManager import MyLoginManager
from cps.cw_login import current_user, login_required, login_user
from cps.cw_login import utils as login_utils
from cps.logout import cleanup_local_logout


REMOTE_ADDR = "192.0.2.10"
SEEDED_USER_AGENT = "CWNG-E2E-seeded-browser"
ISOLATED_USER_AGENT = "CWNG-E2E-signout-browser"


class _NoopActivityDB:
    def log_activity(self, **_kwargs):
        pass


@pytest.fixture
def session_app(monkeypatch):
    engine = create_engine("sqlite://")
    ub.User_Sessions.__table__.create(engine)
    db_session = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr(ub, "session", db_session)
    monkeypatch.setattr(login_utils, "CWA_DB", _NoopActivityDB)
    monkeypatch.setattr(cps.logout.config, "config_login_type", 0, raising=False)

    user = ub.User()
    user.id = 73
    user.name = "seeded-e2e-user"
    user.nickname = user.name
    user.role = constants.ROLE_USER

    app = flask.Flask(__name__)
    app.config.update(SECRET_KEY="test", SESSION_PROTECTION="basic", TESTING=True)
    login_manager = MyLoginManager(app)

    @login_manager.user_loader
    def load_user(user_id, random, session_key):
        stored = (
            db_session.query(ub.User_Sessions)
            .filter(
                ub.User_Sessions.user_id == int(user_id),
                ub.User_Sessions.random == random,
                ub.User_Sessions.session_key == session_key,
            )
            .one_or_none()
        )
        return user if stored is not None else None

    @app.post("/login")
    def login():
        assert login_user(user)
        return "", 204

    @app.post("/logout")
    def logout():
        cleanup_local_logout()
        return "", 204

    @app.get("/protected")
    @login_required
    def protected():
        return {"user_id": current_user.get_id()}

    yield app, db_session

    db_session.remove()
    engine.dispose()


def _request(client, method, path, user_agent):
    return getattr(client, method)(
        path,
        headers={"User-Agent": user_agent},
        environ_overrides={"REMOTE_ADDR": REMOTE_ADDR},
    )


@pytest.mark.unit
def test_same_user_agent_logout_invalidates_both_server_sessions(session_app):
    app, db_session = session_app
    first = app.test_client()
    second = app.test_client()

    assert _request(first, "post", "/login", SEEDED_USER_AGENT).status_code == 204
    assert _request(second, "post", "/login", SEEDED_USER_AGENT).status_code == 204

    rows = db_session.query(ub.User_Sessions).all()
    assert len(rows) == 2
    assert len({row.random for row in rows}) == 2
    assert len({row.session_key for row in rows}) == 1
    assert _request(first, "post", "/logout", SEEDED_USER_AGENT).status_code == 204
    assert db_session.query(ub.User_Sessions).count() == 0
    assert _request(second, "get", "/protected", SEEDED_USER_AGENT).status_code == 401


@pytest.mark.unit
def test_distinct_user_agents_keep_server_sessions_independent(session_app):
    app, db_session = session_app
    seeded = app.test_client()
    isolated = app.test_client()

    assert _request(seeded, "post", "/login", SEEDED_USER_AGENT).status_code == 204
    assert _request(isolated, "post", "/login", ISOLATED_USER_AGENT).status_code == 204

    rows = db_session.query(ub.User_Sessions).all()
    assert len(rows) == 2
    assert len({row.session_key for row in rows}) == 2
    assert _request(isolated, "post", "/logout", ISOLATED_USER_AGENT).status_code == 204
    assert db_session.query(ub.User_Sessions).count() == 1
    assert _request(seeded, "get", "/protected", SEEDED_USER_AGENT).status_code == 200
