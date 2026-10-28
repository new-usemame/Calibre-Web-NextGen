# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A star rating set on a Kobo is saved as the account's personal rating.

The device posts ``/v1/products/<book uuid>/rating/<1..5>`` (empty JSON body;
0 to clear) to whatever URL the init resources give for ``rating``. CWNG used
to leave that on the Kobo Store, so the rating never reached it (#2637).

Requests go through routing, the Kobo auth token and the real init URL.
"""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from werkzeug.datastructures import Headers

from cps import personal_ratings

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    USER_ID, kw, read_column_class,
)

pytestmark = pytest.mark.unit

TOKEN = "reader-token"
OTHER_TOKEN = "other-token"
GUEST_TOKEN = "guest-token"
OTHER_ID, GUEST_ID = USER_ID + 1, USER_ID + 2


class _Wsgi:
    """The world's stand-in for ProxyFix, made callable for the test client."""

    def __init__(self, inner, is_proxied):
        self.inner, self.is_proxied = inner, is_proxied

    def __call__(self, environ, start_response):
        return self.inner(environ, start_response)


def _token(ub, user_id, value):
    row = ub.RemoteAuthToken()
    row.user_id, row.auth_token, row.token_type = user_id, value, 1
    return row


@pytest.fixture
def device(kw, monkeypatch):
    from cps import constants, cw_login, kobo_auth
    from cps.usermanagement import load_user

    kobo, ub = kw.kobo, kw.ub
    for user_id, name, role, token in (
            (OTHER_ID, "other", constants.ROLE_USER, OTHER_TOKEN),
            (GUEST_ID, "Guest", constants.ROLE_ANONYMOUS, GUEST_TOKEN)):
        kw.session.add(ub.User(id=user_id, name=name, email=f"{name}@example.invalid",
                               password="unused", role=role))
        kw.session.add(_token(ub, user_id, token))
    kw.session.add(_token(ub, USER_ID, TOKEN))
    kw.session.commit()

    monkeypatch.setattr(kobo_auth, "limiter", SimpleNamespace(check=lambda: None, current_limits=[]))
    monkeypatch.setattr(kobo, "current_user", cw_login.current_user)
    monkeypatch.setattr(kobo.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", 8083, raising=False)
    kw.hidden = set()
    # Policy (hidden, denied tags, shelves) answers through the access check the
    # web rating API uses; model a book the reader may no longer see there.
    monkeypatch.setattr(personal_ratings, "calibre_db", SimpleNamespace(
        get_book_read_archived=lambda book_id, *args, **kwargs: None if any(
            b.id == book_id and b.uuid in kw.hidden for b in kw.books) else object()))

    manager = cw_login.LoginManager(kw.app)
    manager.user_loader(load_user)
    kw.app.config.update(TESTING=True)
    kw.app.register_blueprint(kobo.kobo)
    kw.app.add_url_rule("/", endpoint="web.index", view_func=lambda: "")  # init builds image_host from it
    kw.app.wsgi_app = _Wsgi(type(kw.app).wsgi_app.__get__(kw.app), is_proxied=True)
    return kw.app.test_client()


def rate(device, book_uuid, rating, token=TOKEN, method="POST"):
    return device.open(f"/kobo/{token}/v1/products/{book_uuid}/rating/{rating}",
                       method=method, json={})


def stored(kw, book, user_id=USER_ID):
    kw.session.expire_all()
    row = kw.session.query(kw.ub.BookRating).filter_by(
        user_id=user_id, book_id=book.id).one_or_none()
    return None if row is None else row.rating


def ratings(kw):
    kw.session.expire_all()
    return kw.session.query(kw.ub.BookRating).count()


def test_stars_are_stored_as_half_star_units(kw, device):
    book = kw.books[0]

    response = rate(device, book.uuid, 4)

    assert response.status_code == 200
    assert response.get_data() == b""
    assert response.content_type == "application/json"
    assert stored(kw, book) == 8
    assert stored(kw, kw.books[1]) is None


def test_rating_again_replaces_the_score_and_zero_clears_it(kw, device):
    book = kw.books[0]

    rate(device, book.uuid, 4)
    rate(device, book.uuid, 5)
    assert stored(kw, book) == 10

    rate(device, book.uuid, 0)
    assert stored(kw, book) == 0
    assert ratings(kw) == 1


def test_a_put_is_accepted_like_a_post(kw, device):
    book = kw.books[0]

    assert rate(device, book.uuid, 3, method="PUT").status_code == 200
    assert stored(kw, book) == 6


def test_each_account_keeps_its_own_rating(kw, device):
    book = kw.books[0]

    rate(device, book.uuid, 3, token=OTHER_TOKEN)
    rate(device, book.uuid, 5)

    assert stored(kw, book) == 10
    assert stored(kw, book, OTHER_ID) == 6


def test_an_unknown_token_is_refused_and_nothing_is_saved(kw, device):
    response = rate(device, kw.books[0].uuid, 4, token="nobody")

    assert response.status_code == 401
    assert ratings(kw) == 0


@pytest.mark.parametrize("rating", ["6", "-1", "x", "3.5"])
def test_an_unusable_rating_is_refused_and_nothing_is_saved(kw, device, rating):
    assert rate(device, kw.books[0].uuid, rating).status_code == 400
    assert ratings(kw) == 0


def test_the_guest_account_has_no_personal_rating(kw, device):
    response = rate(device, kw.books[0].uuid, 4, token=GUEST_TOKEN)

    assert response.status_code == 403
    assert ratings(kw) == 0


@pytest.mark.parametrize("proxied", [False, True])
def test_a_book_hidden_from_the_reader_is_not_rated_or_sent_to_the_store(
        kw, device, monkeypatch, proxied):
    # A book synced before policy hid it: the device still holds it and will
    # keep retrying a failed write, so it gets a 200, and its library uuid
    # never goes to the Kobo Store.
    book = kw.books[0]
    kw.hidden.add(book.uuid)
    sent = []
    monkeypatch.setattr(kw.kobo.config, "config_kobo_proxy", proxied, raising=False)
    monkeypatch.setattr(kw.kobo, "make_request_to_kobo_store",
                        lambda: sent.append(kw.kobo.request.path))

    response = rate(device, book.uuid, 4)

    assert response.status_code == 200
    assert sent == []
    assert ratings(kw) == 0


def test_a_book_cwng_does_not_serve_is_answered_but_not_kept_when_the_proxy_is_off(kw, device):
    # A 404 on a device write makes the Kobo retry it every sync (see
    # HandleTagUpdate); with the proxy off there is nowhere to send it.
    response = rate(device, "11111111-2222-3333-4444-555555555555", 5)

    assert response.status_code == 200
    assert ratings(kw) == 0


def test_a_store_book_goes_to_the_store_when_the_proxy_is_on(kw, device, monkeypatch):
    sent = []
    monkeypatch.setattr(kw.kobo.config, "config_kobo_proxy", True, raising=False)
    monkeypatch.setattr(
        kw.kobo, "make_request_to_kobo_store",
        lambda: sent.append(kw.kobo.request.path) or SimpleNamespace(
            content=b"", status_code=200, headers=Headers()))

    response = rate(device, "11111111-2222-3333-4444-555555555555", 5)

    assert response.status_code == 200
    assert sent == [f"/kobo/{TOKEN}/v1/products/11111111-2222-3333-4444-555555555555/rating/5"]
    assert ratings(kw) == 0


@pytest.mark.parametrize("failure, status", [
    (requests.exceptions.Timeout, 504),
    (requests.exceptions.ConnectionError, 502),
])
def test_a_store_that_cannot_answer_is_a_gateway_error_not_a_500(
        kw, device, monkeypatch, failure, status):
    def down():
        raise failure("storeapi unreachable")

    monkeypatch.setattr(kw.kobo.config, "config_kobo_proxy", True, raising=False)
    monkeypatch.setattr(kw.kobo, "make_request_to_kobo_store", down)

    assert rate(device, "11111111-2222-3333-4444-555555555555", 5).status_code == status


def test_a_failed_save_answers_503_and_keeps_the_old_score(kw, device, monkeypatch):
    book = kw.books[0]
    rate(device, book.uuid, 3)

    def rolled_back():
        kw.session.rollback()
        return False

    monkeypatch.setattr(kw.ub, "session_commit", rolled_back)
    response = rate(device, book.uuid, 5)

    assert response.status_code == 503
    assert stored(kw, book) == 6


@pytest.mark.parametrize("proxied", [True, False])
def test_the_device_is_told_to_rate_through_cwng_and_that_url_works(kw, device, proxied):
    kw.app.wsgi_app.is_proxied = proxied
    book = kw.books[0]

    init = device.get(f"/kobo/{TOKEN}/v1/initialization")
    assert init.status_code == 200, init.get_data(as_text=True)
    template = init.get_json()["Resources"]["rating"]

    assert template.startswith("http")
    assert "storeapi.kobo.com" not in template
    assert template.endswith(f"/kobo/{TOKEN}/v1/products/{{ProductId}}/rating/{{Rating}}")

    path = urlsplit(template.replace("{ProductId}", book.uuid).replace("{Rating}", "4")).path
    assert device.post(path, json={}).status_code == 200
    assert stored(kw, book) == 8
