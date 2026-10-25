# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A star rating set on a Kobo is saved as the account's personal rating.

The device posts ``/v1/products/<book uuid>/rating/<1..5>`` (empty JSON body;
0 to clear) to whatever URL the init resources give for ``rating``. CWNG used
to leave that on the Kobo Store, so the rating never reached it (#2637).
"""

from types import SimpleNamespace

import pytest

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    USER_ID, kw, read_column_class,
)

pytestmark = pytest.mark.unit


def rate(kw, book_uuid, rating, method="POST"):
    path = f"/v1/products/{book_uuid}/rating/{rating}"
    with kw.app.test_request_context(path, method=method, json={}):
        return kw.kobo.HandleRatingRequest.__wrapped__(book_uuid, rating)


def stored(kw, book, user_id=USER_ID):
    kw.session.expire_all()
    row = kw.session.query(kw.ub.BookRating).filter_by(
        user_id=user_id, book_id=book.id).one_or_none()
    return None if row is None else row.rating


def test_stars_are_stored_as_half_star_units(kw):
    book = kw.books[0]

    response = rate(kw, book.uuid, 4)

    assert response.status_code == 200
    assert response.get_data() == b""
    assert response.content_type == "application/json"
    assert stored(kw, book) == 8
    assert stored(kw, kw.books[1]) is None


def test_rating_again_replaces_the_score_and_zero_clears_it(kw):
    book = kw.books[0]

    rate(kw, book.uuid, 4)
    rate(kw, book.uuid, 5)
    assert stored(kw, book) == 10

    rate(kw, book.uuid, 0)
    assert stored(kw, book) == 0
    assert kw.session.query(kw.ub.BookRating).count() == 1


def test_another_accounts_rating_is_left_alone(kw):
    book = kw.books[0]
    kw.session.add(kw.ub.User(id=USER_ID + 1, name="other",
                              email="other@example.invalid", role=0))
    kw.session.add(kw.ub.BookRating(user_id=USER_ID + 1, book_id=book.id, rating=6))
    kw.session.commit()

    rate(kw, book.uuid, 5)

    assert stored(kw, book) == 10
    assert stored(kw, book, USER_ID + 1) == 6


@pytest.mark.parametrize("rating", ["6", "-1", "x", "3.5"])
def test_an_unusable_rating_is_refused_and_nothing_is_saved(kw, rating):
    response = rate(kw, kw.books[0].uuid, rating)

    assert response.status_code == 400
    assert kw.session.query(kw.ub.BookRating).count() == 0


def test_a_put_is_accepted_like_a_post(kw):
    book = kw.books[0]

    assert rate(kw, book.uuid, 3, method="PUT").status_code == 200
    assert stored(kw, book) == 6


def test_a_book_cwng_does_not_hold_goes_to_the_store_path_and_is_not_saved(kw, monkeypatch):
    forwarded = []
    monkeypatch.setattr(kw.kobo, "redirect_or_proxy_request",
                        lambda: forwarded.append(True) or "forwarded")

    result = rate(kw, "11111111-2222-3333-4444-555555555555", 5)

    assert result == "forwarded" and forwarded == [True]
    assert kw.session.query(kw.ub.BookRating).count() == 0


def test_a_failed_save_rolls_back_and_answers_503(kw, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from werkzeug.exceptions import ServiceUnavailable

    book = kw.books[0]
    rate(kw, book.uuid, 3)

    def busy(*_a, **_k):
        raise OperationalError("commit", {}, Exception("busy"))

    monkeypatch.setattr(kw.session, "commit", busy)
    with pytest.raises(ServiceUnavailable):
        rate(kw, book.uuid, 5)
    monkeypatch.undo()
    assert stored(kw, book) == 6


@pytest.mark.parametrize("proxied", [True, False])
def test_init_points_the_rating_resource_at_cwng(kw, monkeypatch, proxied):
    kobo = kw.kobo
    seen = {}

    def fake_url_for(endpoint, **values):
        seen[endpoint] = values
        return f"/kobo/TOKEN/{endpoint}"

    monkeypatch.setattr(kobo, "url_for", fake_url_for)
    monkeypatch.setattr(kobo.kobo_auth, "get_auth_token", lambda: "TOKEN")
    monkeypatch.setattr(kobo.user_library, "mark_response_user_specific",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(kobo.user_cover, "kobo_resource_version_for_user",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(kobo.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", 8083, raising=False)
    monkeypatch.setattr(kobo, "hardcover", None, raising=False)
    kw.app.wsgi_app = SimpleNamespace(is_proxied=proxied)

    with kw.app.test_request_context("/v1/initialization", base_url="http://pi.lan:8093"):
        response = kobo.HandleInitRequest.__wrapped__()

    rating = response.get_json()["Resources"]["rating"]
    assert rating.endswith("/kobo/TOKEN/kobo.HandleRatingRequest")
    assert "storeapi.kobo.com" not in rating
    assert seen["kobo.HandleRatingRequest"]["book_uuid"] == "{ProductId}"
    assert seen["kobo.HandleRatingRequest"]["rating"] == "{Rating}"
