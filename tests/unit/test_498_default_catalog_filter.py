"""Regression coverage for issue #498's per-user default catalog filter."""

from types import SimpleNamespace


def test_me_payload_exposes_saved_default_catalog_filter(monkeypatch):
    from cps.api import auth

    monkeypatch.setattr(auth, "serialize_user", lambda _user: {"id": 7, "name": "reader"})
    monkeypatch.setattr(auth, "_server_features", lambda: {})
    monkeypatch.setattr(auth, "_instance_name", lambda: "Library")
    monkeypatch.setattr(auth, "_user_avatar", lambda _name: None)

    user = SimpleNamespace(
        name="reader",
        view_settings={"catalog": {"default_filter": {"exclude_tag": [12], "read_status": "all"}}},
    )

    assert auth._me_payload(user)["catalog"]["default_filter"] == {"exclude_tag": [12], "read_status": "all"}


def test_me_payload_normalizes_invalid_default_catalog_filter(monkeypatch):
    from cps.api import auth

    monkeypatch.setattr(auth, "serialize_user", lambda _user: {"id": 7, "name": "reader"})
    monkeypatch.setattr(auth, "_server_features", lambda: {})
    monkeypatch.setattr(auth, "_instance_name", lambda: "Library")
    monkeypatch.setattr(auth, "_user_avatar", lambda _name: None)

    user = SimpleNamespace(name="reader", view_settings={"catalog": {"default_filter": 42}})

    assert auth._me_payload(user)["catalog"]["default_filter"] is None
