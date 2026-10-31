# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A reading push answers before Hardcover does (#2775).

Every matched KOReader or Kobo progress push called Hardcover inline: the
client's privacy lookup, ``get_user_book``, maybe ``add_book`` or a status
change, then the progress mutation, each a round trip of about 0.8 s from the
reporter's Pi. The PUT answered after 2.7 to 5 s, the CWNGSync plugin gave up
at 2 s, and the device said "the server took too long" for a reading the
server had stored. This app runs gevent without monkey-patching, so the same
blocking calls also held up every other request for those seconds.

The push now hands Hardcover a plain snapshot and returns; one worker thread
talks to Hardcover, oldest first, keeping only the newest pending reading of
each book so a slow Hardcover can never apply an older place over a newer one.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _BlockingHardcover:
    """Stands in for ``cps.services.hardcover``: every call waits for ``release``."""

    class MissingHardcoverToken(Exception):
        pass

    def __init__(self):
        self.release = threading.Event()
        self.calls = []
        self.started = threading.Event()
        outer = self

        class HardcoverClient:
            def __init__(self, token):
                if not token:
                    raise outer.MissingHardcoverToken()
                self.token = token

            def update_reading_progress(self, identifiers, percent):
                outer.started.set()
                outer.release.wait(10)
                outer.calls.append((self.token, identifiers, percent))

        self.HardcoverClient = HardcoverClient


class _LazyIdentifiers:
    """An ORM relationship: reading it off the request thread is the bug."""

    def __init__(self, owner_thread, rows):
        self.owner_thread = owner_thread
        self.rows = rows

    def __iter__(self):
        assert threading.current_thread() is self.owner_thread, (
            "book.identifiers was read on the Hardcover worker thread; the ORM "
            "session belongs to the request")
        return iter(self.rows)


@pytest.fixture
def kobo(monkeypatch):
    from cps import kobo as module
    from cps.services import hardcover_progress

    fake = _BlockingHardcover()
    monkeypatch.setattr(module, "hardcover", fake)
    monkeypatch.setattr(module.config, "hardcover_sync_enabled", lambda: True, raising=False)
    no_blacklist = MagicMock()
    no_blacklist.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(module, "ub", SimpleNamespace(
        session=no_blacklist, HardcoverBookBlacklist=MagicMock()))
    hardcover_progress.reset_for_tests()
    yield module, fake, hardcover_progress
    fake.release.set()
    hardcover_progress.wait_idle(5)
    hardcover_progress.reset_for_tests()


def _book(book_id, value="hc-1"):
    rows = [SimpleNamespace(type="hardcover-id", val=value),
            SimpleNamespace(type="isbn", val="9780000000000")]
    return SimpleNamespace(id=book_id, identifiers=_LazyIdentifiers(threading.current_thread(), rows))


def _user(name="reader", token="tok"):
    return SimpleNamespace(id=1, name=name, hardcover_token=token)


def _push_within(module, user, book, percent):
    """Push on this (the request) thread and return how long the call took."""
    book.identifiers.owner_thread = threading.current_thread()
    started = time.monotonic()
    module.push_reading_state_to_hardcover(user, book, percent)
    return time.monotonic() - started


@pytest.mark.unit
def test_the_push_returns_while_hardcover_is_still_answering(kobo):
    module, fake, queue = kobo
    elapsed = _push_within(module, _user(), _book(7), 42)

    assert elapsed < 1.0, (
        f"push_reading_state_to_hardcover took {elapsed:.1f}s with Hardcover not "
        "answering; the device's PUT waits on it and times out at 2 s (#2775)")
    assert fake.started.wait(5), "Hardcover was never called"
    fake.release.set()
    assert queue.wait_idle(5)
    assert fake.calls == [("tok", {"hardcover-id": "hc-1"}, 42)], (
        "Hardcover gets the user's token, the book's hardcover identifiers as "
        "plain values, and the percentage")


@pytest.mark.unit
def test_a_slow_hardcover_never_ends_on_an_older_reading(kobo):
    module, fake, queue = kobo
    _push_within(module, _user(), _book(7), 40)
    assert fake.started.wait(5)
    # Hardcover is still busy with 40 while the reader turns pages.
    _push_within(module, _user(), _book(7), 50)
    _push_within(module, _user(), _book(7), 60)
    _push_within(module, _user(), _book(8, "hc-2"), 10)
    fake.release.set()
    assert queue.wait_idle(5)

    book7 = [c[2] for c in fake.calls if c[1] == {"hardcover-id": "hc-1"}]
    assert book7[-1] == 60, f"book 7 ended at {book7[-1]} on Hardcover, not the newest 60"
    assert book7 == sorted(book7), f"readings reached Hardcover out of order: {book7}"
    assert 50 not in book7, "a reading superseded while waiting is not sent at all"
    assert ("tok", {"hardcover-id": "hc-2"}, 10) in fake.calls, "another book's reading is kept"


@pytest.mark.unit
def test_a_failing_hardcover_call_does_not_stop_later_pushes(kobo):
    module, fake, queue = kobo
    original = fake.HardcoverClient.update_reading_progress
    failed = []

    def flaky(self, identifiers, percent):
        if not failed:
            failed.append(percent)
            raise RuntimeError("Hardcover 502")
        return original(self, identifiers, percent)

    fake.HardcoverClient.update_reading_progress = flaky
    fake.release.set()
    _push_within(module, _user(), _book(7), 30)
    assert queue.wait_idle(5)
    _push_within(module, _user(), _book(7), 35)
    assert queue.wait_idle(5)
    assert failed == [30]
    assert fake.calls == [("tok", {"hardcover-id": "hc-1"}, 35)]


@pytest.mark.unit
def test_no_token_means_no_hardcover_work(kobo):
    module, fake, queue = kobo
    fake.release.set()
    _push_within(module, _user(token=None), _book(7), 30)
    assert queue.wait_idle(5)
    assert fake.calls == []


@pytest.mark.unit
def test_a_kosync_put_answers_while_hardcover_is_still_answering(kobo, monkeypatch):
    """The reporter's flow: KOReader PUTs a matched book's reading with
    Hardcover sync on. The PUT must answer inside the plugin's 2 s limit."""
    import sys
    from flask import Flask
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import cps
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    from cps.progress_syncing.models import AppBase

    module, fake, queue = kobo
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(kosync, "ub", MagicMock(session=session))
    monkeypatch.setattr(kosync, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(kosync, "authenticate_user", lambda: _user())
    monkeypatch.setattr(kosync, "update_book_read_status", lambda *_a: None)
    monkeypatch.setattr(kosync, "get_book_checksums", lambda book_id: ["digest-a"] if book_id else [])
    monkeypatch.setattr(kosync, "enrich_response_with_book_info",
                        lambda response, document: (response, 595, "EPUB", "Reporter book", "koreader"))
    book = _book(595)
    monkeypatch.setattr(cps.calibre_db, "get_book", lambda book_id: book, raising=False)

    app = Flask(__name__)
    app.register_blueprint(kosync.kosync)
    started = time.monotonic()
    pushed = app.test_client().put("/kosync/syncs/progress", json={
        "document": "digest-a", "progress": "/body/DocFragment[6]/body/div/text().0",
        "percentage": 0.97, "device": "Kobo Clara BW", "device_id": "clara",
    })
    elapsed = time.monotonic() - started
    session.close()

    assert pushed.status_code == 200
    assert elapsed < 1.0, f"the PUT took {elapsed:.1f}s while Hardcover was not answering"
    assert fake.started.wait(5), "the matched reading never reached Hardcover"
    fake.release.set()
    assert queue.wait_idle(5)
    assert fake.calls == [("tok", {"hardcover-id": "hc-1"}, 97)]
