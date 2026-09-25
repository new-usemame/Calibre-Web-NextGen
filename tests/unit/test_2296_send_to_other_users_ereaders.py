# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An admin can send a book to another user's eReader from the New UI (#2296).

The classic book page has offered this since fork #276: an admin who looks
after a household's eReaders ticks another user's eReader and the book is
emailed there. The New UI's send panel had no way to reach those addresses.
These tests drive the real ``api_v1`` routes with real sessions: the admin is
offered the other users' eReaders, nobody else sees those addresses, and a
book relayed only to someone else is not recorded as the admin's own download.
"""

import pytest

from cps import constants, ub
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    built.enable_web()
    admin = built.add_user("admin")
    admin.role |= constants.ROLE_ADMIN
    admin.kindle_mail = "admin@kindle.com"
    built.add_user("bob").kindle_mail = "bob@kindle.com, bob2@kobo.example"
    built.add_user("carol").kindle_mail = "carol@kindle.com"
    built.add_user("dave")  # no eReader address: nothing to offer
    built.session.commit()
    built.add_book(7, "Dune")
    yield built
    built.close()


def _mail(world, sent):
    from cps import config
    from cps.api import actions

    world.monkeypatch.setattr(config, "get_mail_server_configured", lambda: True, raising=False)

    def fake_send_mail(book_id, book_format, convert, recipients, *_rest):
        sent.append((book_id, book_format, recipients))

    world.monkeypatch.setattr(actions, "send_mail", fake_send_mail)


def _downloads(world, user_name):
    user = world.session.query(ub.User).filter(ub.User.name == user_name).one()
    return [row.book_id for row in world.session.query(ub.Downloads)
            .filter(ub.Downloads.user_id == user.id)]


def test_an_admin_is_offered_every_other_users_ereader(world):
    response = world.browser("admin").get("/api/v1/send-recipients")

    ids = {user.name: user.id for user in world.session.query(ub.User)}
    assert response.status_code == 200
    assert response.get_json()["others"] == [
        {"id": ids["bob"], "name": "bob", "emails": ["bob@kindle.com", "bob2@kobo.example"]},
        {"id": ids["carol"], "name": "carol", "emails": ["carol@kindle.com"]},
    ]


def test_other_users_addresses_stay_hidden_from_everyone_else(world):
    response = world.browser("bob").get("/api/v1/send-recipients")

    assert response.status_code == 200
    assert response.get_json() == {"others": []}


def test_anonymous_callers_get_no_addresses(world):
    response = world.browser().get("/api/v1/send-recipients")

    assert response.status_code == 401


def test_relaying_a_book_to_someone_else_is_not_the_admins_download(world):
    sent = []
    _mail(world, sent)

    response = world.browser("admin").post(
        "/api/v1/books/7/send", json={"format": "epub", "emails": "bob@kindle.com"})

    assert response.status_code == 200, response.get_data(as_text=True)
    assert sent == [(7, "epub", "bob@kindle.com")]
    assert _downloads(world, "admin") == []


def test_a_send_that_includes_your_own_ereader_is_still_your_download(world):
    sent = []
    _mail(world, sent)

    world.browser("admin").post(
        "/api/v1/books/7/send",
        json={"format": "epub", "emails": "Admin@Kindle.com, bob@kindle.com"})
    world.browser("carol").post("/api/v1/books/7/send", json={"format": "epub"})

    assert len(sent) == 2
    assert _downloads(world, "admin") == [7]
    assert _downloads(world, "carol") == [7]


def test_guests_get_no_addresses_when_anonymous_browsing_is_on(world):
    from cps import config

    world.monkeypatch.setattr(config, "config_anonbrowse", 1, raising=False)

    response = world.browser().get("/api/v1/send-recipients")

    assert response.status_code == 401
    assert b"@" not in response.get_data()


def test_the_list_is_never_cached_for_another_user(world):
    from cps import protect_user_specific_catalog_responses

    world.app.after_request(protect_user_specific_catalog_responses)

    response = world.browser("admin").get("/api/v1/send-recipients")

    assert "private" in response.headers.get("Cache-Control", "")


def test_a_relayed_send_still_leaves_an_email_record(world):
    """With no download row for a relay, the activity record is the one trace
    of who sent what, and the classic route has always written it."""
    import types
    from cps import cwa_db_loader

    logged = []

    class FakeCwaDb:
        def log_activity(self, **entry):
            logged.append(entry)

    world.monkeypatch.setattr(cwa_db_loader, "load_cwa_db",
                              lambda: types.SimpleNamespace(CWA_DB=FakeCwaDb))
    _mail(world, [])

    world.browser("admin").post(
        "/api/v1/books/7/send", json={"format": "epub", "emails": "bob@kindle.com"})

    assert [(e["user_name"], e["event_type"], e["item_id"], e["item_title"], e["extra_data"])
            for e in logged] == [("admin", "EMAIL", 7, "Dune", "EPUB")]
