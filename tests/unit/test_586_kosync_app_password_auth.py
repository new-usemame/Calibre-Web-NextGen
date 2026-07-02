# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #586 — per-user app passwords must work
with the KOReader plugin.

Symptom (@alva-seal): using an app password in the KOReader plugin failed
with `KOReader auth: Invalid password for user: <name>` and a 401. OAuth /
LDAP-only users have no local Calibre-Web password, so kosync's
``authenticate_user()`` — which only tried LDAP + the local password hash —
rejected them, even though the OPDS / web Basic-auth path
(``verify_password`` -> ``_verify_app_password``) already accepted app
passwords (fork #95 / #104).

Post-fix: ``authenticate_user()`` also consults ``_verify_app_password``
before failing, so app passwords authenticate KOReader progress AND
annotation sync (they share this login path). The local and LDAP paths are
unchanged (pinned below).

Pattern sources: tests/unit/test_oauth_app_password_auth.py (session
fixture), tests/unit/test_kosync_read_status_thresholds_312.py (module load
via sys.modules to dodge the package re-export shadow).
"""
from __future__ import annotations

import base64
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
KOSYNC_PY = REPO_ROOT / "cps" / "progress_syncing" / "protocols" / "kosync.py"


def _load_kosync():
    import cps.progress_syncing.protocols.kosync  # noqa: F401 — populate sys.modules
    return sys.modules["cps.progress_syncing.protocols.kosync"]


def _basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class _FakeRequest:
    def __init__(self, auth_header: str):
        self.headers = {"Authorization": auth_header}


@pytest.fixture
def in_memory_session():
    from cps.ub import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def kosync_env(monkeypatch, in_memory_session):
    """kosync + usermanagement share ``cps.ub``; point its session at the
    in-memory engine and disable the reverse-proxy / LDAP branches so the
    Basic-auth path is exercised directly."""
    kosync = _load_kosync()
    import cps.ub as ub
    monkeypatch.setattr(ub, "session", in_memory_session, raising=False)
    monkeypatch.setattr(kosync.config, "config_allow_reverse_proxy_header_login", False, raising=False)
    monkeypatch.setattr(kosync.config, "config_login_type", 0, raising=False)  # not LOGIN_LDAP
    return kosync, in_memory_session


def _oauth_user(session):
    from cps.ub import User
    u = User()
    u.name = "alva"
    u.email = "alva@example.com"
    u.password = ""  # OAuth/LDAP-only: no usable local password (the #586 case)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _mint_app_password(session, user, cleartext, revoked=False):
    from cps.ub import UserAppPassword
    row = UserAppPassword(
        user_id=user.id,
        label="kobo",
        password_hash=generate_password_hash(cleartext),
        created_at=datetime.now(timezone.utc),
        revoked=revoked,
    )
    session.add(row)
    session.commit()
    return row


def test_app_password_authenticates_kosync(kosync_env, monkeypatch):
    """The exact #586 symptom: an OAuth user's app password now authenticates."""
    kosync, session = kosync_env
    user = _oauth_user(session)
    _mint_app_password(session, user, "kobodevicetoken")
    monkeypatch.setattr(kosync, "request", _FakeRequest(_basic("alva", "kobodevicetoken")))
    result = kosync.authenticate_user()
    assert result is not None and result.name == "alva"


def test_wrong_app_password_rejected(kosync_env, monkeypatch):
    kosync, session = kosync_env
    user = _oauth_user(session)
    _mint_app_password(session, user, "kobodevicetoken")
    monkeypatch.setattr(kosync, "request", _FakeRequest(_basic("alva", "wrongtoken")))
    assert kosync.authenticate_user() is None


def test_revoked_app_password_rejected(kosync_env, monkeypatch):
    kosync, session = kosync_env
    user = _oauth_user(session)
    _mint_app_password(session, user, "kobodevicetoken", revoked=True)
    monkeypatch.setattr(kosync, "request", _FakeRequest(_basic("alva", "kobodevicetoken")))
    assert kosync.authenticate_user() is None


def test_local_password_path_unchanged(kosync_env, monkeypatch):
    """A normal user with a local password still authenticates — the app-password
    branch is purely additive."""
    kosync, session = kosync_env
    from cps.ub import User
    u = User()
    u.name = "bob"
    u.email = "bob@example.com"
    u.password = generate_password_hash("localpw")
    session.add(u)
    session.commit()
    monkeypatch.setattr(kosync, "request", _FakeRequest(_basic("bob", "localpw")))
    assert kosync.authenticate_user().name == "bob"


def test_no_credentials_still_returns_none(kosync_env, monkeypatch):
    kosync, session = kosync_env
    _oauth_user(session)
    monkeypatch.setattr(kosync, "request", _FakeRequest("Basic " + base64.b64encode(b"alva:").decode()))
    assert kosync.authenticate_user() is None


def test_app_password_tried_before_invalid_password_return():
    """Source pin: the app-password check must live inside authenticate_user and
    precede the invalid-password failure return, or it would be dead code."""
    src = KOSYNC_PY.read_text(encoding="utf-8")
    m = re.search(r"^def authenticate_user\(.*?(?=^def |^class |\Z)", src, re.MULTILINE | re.DOTALL)
    assert m is not None, "authenticate_user not found"
    body = m.group(0)
    assert "_verify_app_password" in body, "kosync auth must consult app passwords (#586)"
    assert body.index("_verify_app_password") < body.index('"KOReader auth: Invalid password'), \
        "app-password check must precede the invalid-password failure return"
