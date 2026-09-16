# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 7: one JSON object per line, per job — and the cost cap.

The file exists rather than a counter in memory for two reasons. A job that is
cancelled, crashes or hits the cap has to be resumable without paying twice, so the
spend has to survive the process. And when a conversion goes wrong, the question is
always "which page, which model, what did the gate say" — which is a file somebody
can read during an incident, not a metric.

Appends are line-atomic and the reader tolerates a truncated final line, so a job
killed mid-write loses at most its last entry rather than its history.
"""

import json
import os
import time
from collections import Counter


class CapExceeded(Exception):
    """The next call would spend past the job's cap. Nothing was sent."""

    def __init__(self, spent, cap, projected):
        self.spent = spent
        self.cap = cap
        self.projected = projected
        super(CapExceeded, self).__init__(
            "cost cap reached: $%.4f spent of $%.2f, and the next page could cost "
            "up to $%.4f at the allowed rates" % (spent, cap, projected))


class Ledger(object):
    """The spend and the evidence for one job."""

    def __init__(self, path, cap_usd, job_id=None):
        self.path = str(path)
        self.cap_usd = float(cap_usd)
        self.job_id = job_id
        self._entries = _read(self.path)

    # ------------------------------------------------------------------ the cap

    def spent(self):
        return round(sum(float(e.get("cost_usd") or 0.0) for e in self._entries), 6)

    def remaining(self):
        return max(0.0, round(self.cap_usd - self.spent(), 6))

    def would_exceed(self, projected_usd):
        return self.spent() + float(projected_usd or 0.0) > self.cap_usd + 1e-9

    def reserve(self, projected_usd):
        """Check before the call, not after the bill."""
        if self.would_exceed(projected_usd):
            raise CapExceeded(self.spent(), self.cap_usd, projected_usd)
        return self.remaining()

    # --------------------------------------------------------------- the record

    def record(self, entry):
        entry = dict(entry)
        entry.setdefault("ts", round(time.time(), 3))
        if self.job_id:
            entry.setdefault("job", self.job_id)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._entries.append(entry)
        return entry

    def entries(self, kind=None):
        if kind is None:
            return list(self._entries)
        return [e for e in self._entries if e.get("kind") == kind]

    def pages_done(self):
        """Pages already paid for — the resume point after a crash or a cap stop."""
        return {int(e["page"]) for e in self._entries if e.get("page") is not None}

    def job(self):
        """What the job records say it is: mode, owner, how it ended.

        The last ``start`` wins and the last ``finish`` wins, so a resumed job reads
        as one row rather than two — the ledger is append-only and a resume appends.
        """
        facts = {"job_id": self.job_id, "status": "running"}
        for entry in self._entries:
            if entry.get("kind") != "job":
                continue
            if entry.get("event") == "start":
                for key in ("mode", "user_id", "tier", "pages", "title", "cap_usd"):
                    if entry.get(key) is not None:
                        facts[key] = entry[key]
                facts["started"] = entry.get("ts")
            elif entry.get("event") == "finish":
                facts["status"] = entry.get("status") or "done"
                facts["finished"] = entry.get("ts")
                if entry.get("error"):
                    facts["error"] = entry["error"]
        return facts

    def summary(self):
        """One row for the job list: what it was, how it ended, what it cost."""
        row = dict(self.totals())
        # The job's own record wins: a ledger reopened to read a finished job does
        # not know the cap that job ran under until the start record says so.
        row.update(self.job())
        return row

    def totals(self):
        gate = Counter(e.get("gate") for e in self._entries if e.get("gate"))
        models = Counter(e.get("model") for e in self._entries if e.get("model"))
        # A page served from the cache is evidence and belongs in the file, but it
        # is not a call: counting it would make a resumed job look like it spent
        # again at $0.00 a page.
        calls = sum(1 for e in self._entries
                    if e.get("cost_usd") is not None and not e.get("cached"))
        reused = sum(1 for e in self._entries if e.get("cached"))
        recovery = next((dict(e) for e in self._entries
                         if e.get("kind") == "recovery"), None)
        return {
            "calls": calls,
            "reused": reused,
            "spend_usd": self.spent(),
            "cap_usd": self.cap_usd,
            "remaining_usd": self.remaining(),
            "prompt_tokens": sum(int(e.get("prompt_tokens") or 0) for e in self._entries),
            "completion_tokens": sum(int(e.get("completion_tokens") or 0) for e in self._entries),
            "gate": dict(gate),
            "models": dict(models),
            "pages": len(self.pages_done()),
            "recovery": recovery or {},
        }


def _read(path):
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                # A job killed mid-write leaves a partial last line. Losing that one
                # entry is right; refusing to open the file at all is not.
                continue
    return entries


def read_summaries(directory, limit=20):
    """The recent jobs for one book, newest first.

    Reading the files rather than keeping a table is deliberate: the ledger already
    has to survive a crash, so it is the record, and a second store could disagree
    with it. ``limit`` is applied after sorting by modification time so a book with
    hundreds of attempts does not read hundreds of files.
    """
    if not os.path.isdir(directory):
        return []
    files = []
    for name in os.listdir(directory):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(directory, name)
        try:
            files.append((os.path.getmtime(path), path, name[:-len(".jsonl")]))
        except OSError:                                           # pragma: no cover
            continue
    files.sort(reverse=True)
    rows = []
    for _mtime, path, job_id in files[:int(limit)]:
        led = Ledger(path, cap_usd=0.0, job_id=job_id)
        led.cap_usd = float(led.job().get("cap_usd") or 0.0)
        rows.append(led.summary())
    return rows
