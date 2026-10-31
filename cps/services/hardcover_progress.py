# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Send reading progress to Hardcover off the request that reported it (#2775).

A progress update takes several sequential Hardcover round trips (privacy,
``get_user_book``, perhaps ``add_book`` or a status change, then the progress
mutation). Inline, that held a device's progress PUT open for 2.7 to 5 s on a
Pi, past the CWNGSync plugin's 2 s limit, and because this app runs gevent
without monkey-patching it also blocked every other request meanwhile.

Callers hand over plain values only (token, identifier dict, percentage): the
worker is a real OS thread and must not touch a request's ORM session.

One worker, oldest first. Each (user, book) keeps only its newest pending
reading, so readings reach Hardcover in the order they were made and a burst
of page turns while Hardcover is slow costs one call, not one per turn.
"""

import threading
from collections import OrderedDict

from .. import logger

log = logger.create()

_LOCK = threading.Condition()
# (user_id, book_id) -> job dict, in arrival order of the key's first pending job
_PENDING = OrderedDict()
_BUSY = False
_WORKER = None


def enqueue(*, user_id, user_name, book_id, token, identifiers, percent, client_factory):
    """Queue one reading for Hardcover and return immediately.

    ``client_factory(token)`` builds the Hardcover client on the worker thread,
    or returns None when there is nothing to send with that token;
    ``identifiers`` is a plain ``{type: value}`` dict.
    """
    global _WORKER
    key = (user_id, book_id)
    job = dict(user_name=user_name, book_id=book_id, token=token,
               identifiers=dict(identifiers), percent=percent,
               client_factory=client_factory)
    with _LOCK:
        if key in _PENDING:
            log.debug("Hardcover: book %s reading %s replaces an unsent %s",
                      book_id, percent, _PENDING[key]["percent"])
        _PENDING[key] = job  # replaces in place, so the key keeps its turn
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_run, name="hardcover-progress", daemon=True)
            _WORKER.start()
        _LOCK.notify_all()


def _run():
    global _BUSY
    while True:
        with _LOCK:
            while not _PENDING:
                _LOCK.wait()
            _key, job = _PENDING.popitem(last=False)
            _BUSY = True
        try:
            _send(job)
        except Exception as e:  # one failed call must not stop the worker
            log.error("Failed to update reading progress for book %s in Hardcover: %s",
                      job["book_id"], e, exc_info=True)
        finally:
            with _LOCK:
                _BUSY = False
                _LOCK.notify_all()


def _send(job):
    client = job["client_factory"](job["token"])
    if client is not None:
        client.update_reading_progress(job["identifiers"], job["percent"])


def wait_idle(timeout):
    """Block until nothing is pending or being sent. True when idle in time."""
    with _LOCK:
        return _LOCK.wait_for(lambda: not _PENDING and not _BUSY, timeout)


def reset_for_tests():
    """Drop unsent readings. The worker, if any, stays parked on the queue."""
    with _LOCK:
        _PENDING.clear()
