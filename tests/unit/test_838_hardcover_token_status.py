import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from cps.services import hardcover


@pytest.mark.parametrize("token", [None, "", 0, [], {}])
def test_missing_or_wrong_type_token_never_calls_network(monkeypatch, token):
    monkeypatch.setattr(hardcover.requests, "post", lambda *_a, **_k: pytest.fail("network called"))
    assert hardcover.token_status(token) == {
        "present": False, "valid": None, "expires_at": None, "expired": None,
    }


def _jwt(exp):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return "{}.{}.sig".format(encode({"alg": "none"}), encode({"exp": exp}))


def test_valid_token_surfaces_expiry(monkeypatch):
    exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": {"me": [{"id": 1}]}}

    monkeypatch.setattr(hardcover.requests, "post", lambda *_a, **kwargs: Response())
    status = hardcover.token_status(_jwt(exp))
    assert status["present"] is True
    assert status["valid"] is True
    assert status["expired"] is False
    assert status["expires_at"]


def test_rejected_token_and_network_unknown(monkeypatch):
    class Rejected:
        status_code = 401
    monkeypatch.setattr(hardcover.requests, "post", lambda *_a, **_k: Rejected())
    assert hardcover.token_status("opaque")["valid"] is False

    def offline(*_a, **_k):
        raise hardcover.requests.ConnectionError("offline")
    monkeypatch.setattr(hardcover.requests, "post", offline)
    assert hardcover.token_status("opaque")["valid"] is None
