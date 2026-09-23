# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The Reflow data folder is kept bounded, and nothing someone needs goes with it.

Everything Reflow writes for its own reuse -- recognised pages, prepared quotes,
estimates, model answers -- and for people -- samples, job records, publication
locks -- used to stay forever. The sweep now removes what its store's policy
says is too old, and past a store's budget the oldest first. The table below is
the contract: every kind of entry there is, how old it is, and whether it stays.
"""

import fcntl
import json
import os
import time

import pytest

from cps.services.reflow import publication, retention

pytestmark = pytest.mark.unit

DAY = 86400
NOW = time.time()
LEGACY = "ab" + "1" * 30
ANSWERED, PENDING, NAMED, GARBLED, PAID = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)


def _jsonl(*entries):
    return "".join(json.dumps(entry) + "\n" for entry in entries)


START_SAMPLE = {"kind": "job", "event": "start", "mode": "sample", "user_id": 7}
START_FULL = {"kind": "job", "event": "start", "mode": "full", "user_id": 7}
FINISH = {"kind": "job", "event": "finish", "status": "done"}

#: (path under REFLOW_DIR, age in days, content, stays?, why)
ENTRIES = [
    # A sample is a preview, not a library: a week, then it goes.
    ("samples/7/aaaaaaaaaaaaaaaa.epub", 8, "PK", False, "an expired sample"),
    ("samples/7/bbbbbbbbbbbbbbbb.epub", 2, "PK", True, "a sample from this week"),
    ("samples/7/cccccccccccccccc.epub", 8, "PK", True, "the sample of a job the worker holds"),
    ("samples/7/.reflow-dddddddddddddddd-staging/candidate.epub", 30, "PK", True,
     "a staging folder: recovery's, not the sweep's"),
    # Reuse stores go by age.
    ("estimates/e1.json", 31, "{}", False, "a month-old estimate"),
    ("estimates/e2.json", 10, "{}", True, "a recent estimate"),
    ("estimates/e2.json.0f0f.tmp", 2, "{", False, "a partial write whose writer is gone"),
    ("estimates/e3.json.0e0e.tmp", 0.05, "{", True, "a partial write in flight"),
    ("typed-quotes/q1.json", 31, "{}", False, "a month-old prepared quote"),
    ("typed-quotes/q2.json", 10, "{}", True, "a recent prepared quote"),
    ("typed-quotes/q2.0a0a.tmp", 2, "{", False, "a partial quote whose writer is gone"),
    ("ocr-cache/o1.json", 61, "{}", False, "a recognised page two months old"),
    ("ocr-cache/o2.json", 59, "{}", True, "a recognised page under two months old"),
    ("ocr-cache/o2-x1y2.tmp", 2, "{", False, "a partial recognition whose writer is gone"),
    # Model answers cost money: half a year, and never one a kept record needs.
    ("cache/ab/%s.json" % LEGACY, 181, "{}", False, "an old answer of the legacy route"),
    ("cache/ab/%s.json" % ("ab" + "2" * 30), 100, "{}", True, "a legacy answer under half a year"),
    ("cache/source-operations-v1/aa/%s.json" % ANSWERED, 181,
     json.dumps({"state": "answered", "request_sha256": ANSWERED}), False,
     "an old answer no kept record names"),
    ("cache/source-operations-v1/bb/%s.json" % PENDING, 400,
     json.dumps({"state": "pending", "request_sha256": PENDING}), True,
     "a pending claim: time is no proof the provider did not bill"),
    ("cache/source-operations-v1/cc/%s.json" % NAMED, 400,
     json.dumps({"state": "answered", "request_sha256": NAMED}), True,
     "an answer the record of a filed EPUB names"),
    ("cache/source-operations-v1/dd/%s.json" % GARBLED, 400, "{not json", True,
     "an unreadable claim: it may be a pending one"),
    ("cache/source-operations-v1/ee/%s.json" % PAID, 400,
     json.dumps({"state": "answered", "request_sha256": PAID}), True,
     "an answer bought for an EPUB that was filed"),
    # Job records: the history of an attempt goes after a year; the rest stays.
    ("jobs/5/1111111111111111.jsonl", 400, _jsonl(START_SAMPLE, FINISH), False,
     "a finished sample's record, a year on, holding nothing"),
    ("jobs/5/2222222222222222.jsonl", 100, _jsonl(START_SAMPLE, FINISH), True,
     "a finished sample's record under a year old"),
    ("jobs/5/1111111111110000.jsonl", 400, _jsonl(
        START_SAMPLE, {"kind": "artifact", "sha256": "f" * 64, "bytes": 10}, FINISH), False,
     "a sample's record a year on: the sample was never filed and is long gone"),
    ("jobs/5/3333333333333333.jsonl", 400, _jsonl(START_SAMPLE), True,
     "a record that still reads running: recovery settles it first"),
    ("jobs/5/4444444444444444.jsonl", 400, _jsonl(
        START_SAMPLE, {"kind": "reservation", "event": "pending", "attempt": "a1",
                       "bound_usd": 0.02}, FINISH), True,
     "a record holding a request whose billing is unresolved"),
    ("jobs/5/5555555555555555.jsonl", 400, _jsonl(
        START_SAMPLE, {"page": 3, "cost_usd": 0.01}, FINISH), True,
     "a record of money spent"),
    ("jobs/5/6666666666666666.jsonl", 400, _jsonl(
        START_FULL, {"kind": "typed_stage", "request_sha256": NAMED, "cached": True},
        {"kind": "artifact", "sha256": "f" * 64, "bytes": 10}, FINISH), True,
     "the record of a filed EPUB"),
    ("jobs/6/6666666666660000.jsonl", 400, _jsonl(
        START_FULL, {"kind": "reservation", "event": "pending", "attempt": "b2",
                     "bound_usd": 0.02, "request_sha256": PAID},
        {"kind": "reservation", "event": "reconciled", "attempt": "b2", "cost_usd": 0.01},
        {"kind": "typed_stage", "request_sha256": PAID, "attempt": "b2", "status": "answered"},
        {"kind": "artifact", "sha256": "f" * 64, "bytes": 10}, FINISH), True,
     "the record of an EPUB filed after a paid review"),
    ("jobs/5/7777777777777777.jsonl", 400, _jsonl(
        START_FULL, {"kind": "publication", "event": "prepared"},
        {"kind": "publication", "event": "rolled_back"}, FINISH), True,
     "a publication journal"),
    ("jobs/5/8888888888888888.jsonl", 400, _jsonl(
        START_SAMPLE, {"kind": "staging", "event": "created", "root": "reflow",
                       "path": "samples/7/.reflow-8888888888888888-staging"}, FINISH), True,
     "a record naming a staging folder recovery has not removed"),
    # Locks go after a month unused; conflicts never.
    ("publication-locks/%s.lock" % ("1" * 64), 31, "", False, "a lock unused for a month"),
    ("publication-locks/%s.lock" % ("2" * 64), 10, "", True, "a lock used this month"),
    ("publication-conflicts/5/9999999999999999/manifest.json", 1000, "{}", True,
     "a conflict's evidence is for a person"),
]
#: Scratch folders: (path, age in days, stays?)
SCRATCH = [
    ("ocr-scratch/reflow-ocr-old", 2, False),
    ("ocr-scratch/reflow-ocr-new", 0.05, True),
    ("ocr-scratch/unrelated", 5, True),
    ("quote-scratch/prepare-old", 2, False),
]


def _age(path, days):
    when = NOW - days * DAY
    os.utime(path, (when, when))


def _populate(root):
    for relative, days, content, _stays, _why in ENTRIES:
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        _age(path, days)
    for relative, days, _stays in SCRATCH:
        path = os.path.join(root, relative)
        os.makedirs(path)
        with open(os.path.join(path, "page.jpg"), "wb") as handle:
            handle.write(b"\xff\xd8")
        _age(path, days)


def test_a_sweep_removes_what_nothing_needs_and_keeps_everything_else(tmp_path):
    root = str(tmp_path)
    _populate(root)

    removed = retention.sweep(root, active_jobs={"cccccccccccccccc"}, now=NOW)

    wrong = ["%s (%s): %s" % (relative, why, "removed" if stays else "kept")
             for relative, _days, _content, stays, why in ENTRIES
             if os.path.exists(os.path.join(root, relative)) != stays]
    wrong += ["%s: %s" % (relative, "removed" if stays else "kept")
              for relative, _days, stays in SCRATCH
              if os.path.exists(os.path.join(root, relative)) != stays]
    assert not wrong, "\n".join(wrong)
    expected = {}
    for relative, _days, _content, stays, _why in ENTRIES:
        if not stays:
            store = relative.split("/")[0]
            expected[store] = expected.get(store, 0) + 1
    for relative, _days, stays in SCRATCH:
        if not stays:
            store = relative.split("/")[0]
            expected[store] = expected.get(store, 0) + 1
    assert removed == expected


def test_a_store_over_its_budget_loses_its_oldest_first_and_never_a_fresh_file(
        tmp_path, monkeypatch):
    """Two recognised books fit, a third does not: the oldest goes. A store that
    is over budget only because of today's work keeps it -- a book being read
    right now is not the one to throw away."""
    monkeypatch.setitem(retention.POLICIES, "ocr-cache", retention.Policy(60, max_mb=1))
    root = str(tmp_path)
    ages = {"oldest": 20, "older": 10, "yesterday": 2, "today": 0.1, "now": 0.01}
    for name, days in ages.items():
        path = os.path.join(root, "ocr-cache", name + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"0" * 300 * 1024)
        _age(path, days)

    assert retention.sweep(root, now=NOW) == {"ocr-cache": 2}
    left = sorted(name[:-5] for name in os.listdir(os.path.join(root, "ocr-cache")))
    # 1.5 MB against 1 MB: the two oldest go (0.9 MB left). Today's two files
    # would have been next, and are never candidates for size.
    assert left == ["now", "today", "yesterday"]

    for name in ("yesterday",):
        os.remove(os.path.join(root, "ocr-cache", name + ".json"))
    with open(os.path.join(root, "ocr-cache", "big.json"), "wb") as handle:
        handle.write(b"0" * 1024 * 1024)
    assert retention.sweep(root, now=NOW) == {}, "only today's work is over the budget"


def test_a_lock_someone_holds_is_never_removed(tmp_path):
    locks, target = str(tmp_path / "publication-locks"), str(tmp_path / "book.epub")
    with publication.lock(locks, target) as held:
        assert held
        (name,) = os.listdir(locks)
        _age(os.path.join(locks, name), 40)
        assert retention.sweep(str(tmp_path), now=NOW) == {}
        assert os.listdir(locks) == [name]


def test_a_lock_file_removed_while_a_publisher_waits_for_it_still_admits_one_publisher(
        tmp_path, monkeypatch):
    """The race that makes removing lock files dangerous at all. A publisher opens
    the lock file; before it takes the lock, the sweep takes it, finds it a month
    unused and removes it. The publisher then locks a file nobody else can find,
    and the next publisher creates a new one and locks that: two publishers of one
    EPUB at once. ``publication.lock`` checks, once it holds a lock, that the name
    still leads to the file it locked, and takes the lock again if not."""
    locks, target = str(tmp_path / "publication-locks"), str(tmp_path / "book.epub")
    with publication.lock(locks, target):
        pass
    (name,) = os.listdir(locks)
    _age(os.path.join(locks, name), 40)
    real, swept = fcntl.flock, []

    def flock(handle, operation):
        # The sweep runs between the publisher's open and its lock, once.
        if operation & fcntl.LOCK_EX and not swept:
            swept.append(None)
            swept[0] = retention.sweep(str(tmp_path), now=NOW)
        return real(handle, operation)

    monkeypatch.setattr(fcntl, "flock", flock)
    with publication.lock(locks, target) as first:
        assert first
        with publication.lock(locks, target, blocking=False) as second:
            assert second is False, "two publishers hold the lock on one EPUB"
    assert swept == [{"publication-locks": 1}], "the sweep did not remove the file"
