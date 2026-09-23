# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Keep the Reflow data folder bounded without losing anything someone needs.

Reflow keeps things under ``REFLOW_DIR`` so it never does work twice --
recognised pages, prepared quotes, estimates, model answers -- and things people
use: a sample to download, the record of each job, the lock a publication takes.
Nothing ever removed any of it, so the folder grew with every book anyone looked
at. :func:`sweep` removes, store by store, what is older than the store's policy
allows and then, oldest first, what takes the store over its size budget. It
never removes what something still needs:

* a job's record while the job is waiting or running, or once it holds money (a
  charge, or a request whose billing is unresolved), a filed EPUB, a publication
  journal, or a staging folder recovery has yet to take away;
* a model answer that a running job's record or a filed EPUB's record names, or
  a request claim that is still pending: time passing is no proof that a
  provider did not bill;
* the sample of a job still in the worker, or anything inside a staging folder
  (recovery owns those);
* a publication lock someone holds -- a lock file is only removed while the
  sweep holds it, and ``publication.lock`` re-checks the file it locked;
* anything in ``publication-conflicts``: that is evidence for a person.

A partial write (``*.tmp``) or a scratch folder is removed once it is a day old:
every writer finishes, or is stopped, long before that. Nothing younger than a
day is removed to meet a size budget.
"""

import fcntl
import json
import logging
import os
import shutil
import stat
import time
from dataclasses import dataclass
from typing import Optional

from . import ledger as ledger_mod

log = logging.getLogger(__name__)

DAY = 86400.0
#: A partial write or scratch folder older than this belongs to a process that
#: is gone: OCR stops a page after a minute, a preparation after half an hour.
LEFTOVER_DAYS = 1.0


@dataclass(frozen=True)
class Policy:
    #: Entries older than this many days are removed.
    days: float
    #: Past this many megabytes, the oldest removable entries go first.
    max_mb: Optional[float] = None


POLICIES = {
    # A sample is a preview, not a library.
    "samples": Policy(7, 2048),
    # Keyed by the PDF's size, time and the price table: rarely read twice.
    "estimates": Policy(30, 64),
    # Bound to versions and prices that move; preparing one again costs CPU.
    "typed-quotes": Policy(30, 512),
    # Recognising a page again costs seconds of CPU, not money.
    "ocr-cache": Policy(60, 2048),
    # Model answers cost money: kept longest.
    "cache": Policy(180, 1024),
    # What is left of a record with no money, filed EPUB or journal in it is
    # the history of an attempt.
    "jobs": Policy(365, 256),
    # Empty files, one per EPUB ever published: only their age matters.
    "publication-locks": Policy(30),
}
#: Per-process working folders, removed whole once they are a day old; only
#: folders with the name their writer gives them.
SCRATCH = {"ocr-scratch": "reflow-ocr-", "quote-scratch": "prepare-"}


def sweep(root, active_jobs=(), now=None):
    """Apply :data:`POLICIES` under ``root``. Returns ``{store: entries removed}``.

    ``active_jobs`` are the ids of jobs the worker holds (waiting or running):
    their record and sample are never touched.
    """
    now = time.time() if now is None else float(now)
    active = set(active_jobs or ())
    removed = {}

    # The job records first: they decide which model answers are still named.
    needed_records, named_answers = {}, set()
    records = _entries(os.path.join(root, "jobs"), ".jsonl")
    for path, _size, _mtime in records:
        job_id = os.path.basename(path)[:-len(".jsonl")]
        try:
            led = ledger_mod.Ledger(path, cap_usd=0.0, job_id=job_id)
            reason, answers = _record_needs(led, active)
        except (OSError, ValueError, TypeError, AttributeError):
            needed_records[path] = "unreadable"          # never guess at a record
            continue
        if reason:
            needed_records[path] = reason
        if answers:
            named_answers.update(e["request_sha256"] for e in led.entries()
                                 if isinstance(e.get("request_sha256"), str))

    def sample_needed(path):
        return os.path.basename(path)[:-len(".epub")] in active

    def answer_needed(path):
        if os.path.basename(os.path.dirname(os.path.dirname(path))) != "source-operations-v1":
            return False                         # a legacy page answer: reuse only
        key = os.path.basename(path)[:-len(".json")]
        if key in named_answers:
            return True
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle).get("state")
        except (OSError, ValueError, AttributeError):
            return True                          # unreadable: a claim may be pending
        return state not in ("answered", "rejected")

    stores = (
        ("samples", ".epub", sample_needed),
        ("estimates", ".json", None),
        ("typed-quotes", ".json", None),
        ("ocr-cache", ".json", None),
        ("cache", ".json", answer_needed),
        ("jobs", ".jsonl", lambda path: path in needed_records),
    )
    for store, suffix, needed in stores:
        directory = os.path.join(root, store)
        entries = records if store == "jobs" else _entries(directory, suffix)
        count = _leftovers(directory, now)
        count += _prune(entries, POLICIES[store], now, needed or (lambda path: False))
        if count:
            removed[store] = count

    count = _prune_locks(os.path.join(root, "publication-locks"),
                         POLICIES["publication-locks"], now)
    if count:
        removed["publication-locks"] = count
    for store, prefix in SCRATCH.items():
        count = _scratch(os.path.join(root, store), prefix, now)
        if count:
            removed[store] = count
    return removed


def _record_needs(led, active):
    """Why a job's record must stay (or None), and whether its answers must.

    A record that still reads running is recovery's to settle; money is a
    billing record; a publication journal, or a whole-book job's artifact, is the
    provenance of an EPUB filed in the library -- and so are the model answers it
    names.
    """
    facts = led.job()
    entries = led.entries()
    if led.job_id in active or facts.get("status") == "running":
        return "running", True
    filed = any(e.get("kind") == "publication" for e in entries) or (
        facts.get("mode") == "full" and any(e.get("kind") == "artifact" for e in entries))
    if any(e.get("kind") == "reservation" for e in entries) or led.spent() > 0:
        return "money", filed
    if filed:
        return "filed", True
    staged = {e.get("path") for e in entries
              if e.get("kind") == "staging" and e.get("event") == "created"}
    staged -= {e.get("path") for e in entries
               if e.get("kind") == "staging" and e.get("event") == "removed"}
    if staged:
        return "staging", False
    return None, False


def _entries(directory, suffix):
    """``(path, bytes, mtime)`` of every file named ``*suffix`` under ``directory``.

    Staging folders (``.reflow-*``) are recovery's and symlinks nobody's, so
    neither is entered or listed.
    """
    found = []
    for folder, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and not os.path.islink(os.path.join(folder, d))]
        for name in files:
            if not name.endswith(suffix) or name.startswith("."):
                continue
            path = os.path.join(folder, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append((path, info.st_size, info.st_mtime))
    return found


def _prune(entries, policy, now, needed):
    """Remove what is too old, then the oldest until the store fits its budget."""
    cutoff = now - policy.days * DAY
    settled = now - LEFTOVER_DAYS * DAY
    total = sum(size for _path, size, _mtime in entries)
    budget = float("inf") if policy.max_mb is None else policy.max_mb * 1024 * 1024
    count = 0
    for path, size, mtime in sorted(entries, key=lambda entry: entry[2]):
        if mtime >= cutoff and (total <= budget or mtime >= settled):
            continue
        if needed(path):
            continue
        if _unlink(path):
            total -= size
            count += 1
    return count


def _leftovers(directory, now):
    """Partial writes (``*.tmp``) older than a day: their writer is gone."""
    count = 0
    for path, _size, mtime in _entries(directory, ".tmp"):
        if mtime < now - LEFTOVER_DAYS * DAY and _unlink(path):
            count += 1
    return count


def _scratch(directory, prefix, now):
    """A day-old working folder of a recognizer or preparation that is gone."""
    count = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(directory, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not name.startswith(prefix) or not stat.S_ISDIR(info.st_mode):
            continue
        if info.st_mtime < now - LEFTOVER_DAYS * DAY:
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.lexists(path):
                count += 1
    return count


def _prune_locks(directory, policy, now):
    """Remove lock files unused for ``policy.days``, each only while holding it.

    ``publication.lock`` refreshes a lock file's time whenever it is taken, and
    re-checks after taking it that the name still leads to the file it locked; a
    publisher that opened a file this removes locks the new one instead.
    """
    cutoff = now - policy.days * DAY
    count = 0
    for path, _size, mtime in _entries(directory, ".lock"):
        if mtime >= cutoff:
            continue
        try:
            handle = open(path, "r")
        except OSError:
            continue
        with handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue                               # someone holds it
            try:
                current, held = os.lstat(path), os.fstat(handle.fileno())
                if (current.st_dev, current.st_ino) == (held.st_dev, held.st_ino) \
                        and current.st_mtime < cutoff:
                    os.unlink(path)
                    count += 1
            except OSError:
                pass
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return count


def _unlink(path):
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("reflow retention: could not remove %s: %s", path, exc)
        return False


__all__ = ["sweep", "POLICIES", "Policy", "SCRATCH", "LEFTOVER_DAYS"]
