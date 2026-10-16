# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Book-scoped admission for Reflow conversions.

The worker's task list can answer "is this book being converted?", but the API was
reading that answer and then enqueueing as two separate moments -- and two requests
inside the same gap both saw a free book and both spent money on it. This registry
makes the decision atomic: a book is reserved before its task is enqueued, the
reservation is held through the enqueue and the whole run, and it ends when the
task reaches a terminal state.

The reservation is the book's, not the user's and not the mode's: a sample started
by one user holds the book against a full conversion started by another, because
both would spend on the same pages.

gevent note: CWNG runs gevent WITHOUT monkey.patch_all(), and the standing rule is
no threading lock held across I/O. The lock below guards only dictionary reads and
writes -- no filesystem, no network, no logging -- so it cannot park the hub.
"""

import threading

from cps.services.worker import STAT_STARTED, STAT_WAITING

_lock = threading.Lock()
_reservations = {}


#: The states in which a reserved task still legitimately holds its book. Anything
#: else -- finished, failed, ended, cancelled -- means the entry is stale: it is
#: evicted on the way past rather than blocking the book forever.
_ACTIVE = (STAT_WAITING, STAT_STARTED)


def reserve(book_id, worker, task):
    """Atomically check and hold the book for this task. True when taken.

    An entry whose task has left the active states -- including a task cancelled
    while it waited, which never reaches ``run()`` and so never calls ``release``
    -- is stale and is evicted here. An entry recorded against a different worker
    instance can only be a leftover from another life of the queue, so it is
    evicted too.
    """
    book_id = int(book_id)
    with _lock:
        entry = _reservations.get(book_id)
        if entry is not None:
            if entry["worker"] is worker \
                    and getattr(entry["task"], "stat", None) in _ACTIVE:
                return False
            del _reservations[book_id]
        _reservations[book_id] = {"task": task, "worker": worker}
        return True


def release(book_id, task):
    """Give the book back. Only the reservation's own task may release it."""
    book_id = int(book_id)
    with _lock:
        entry = _reservations.get(book_id)
        if entry is not None and entry["task"] is task:
            del _reservations[book_id]
