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
            "cost cap reached: $%.4f spent of $%.2f, next page needs about $%.4f"
            % (spent, cap, projected))


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

    def totals(self):
        gate = Counter(e.get("gate") for e in self._entries if e.get("gate"))
        models = Counter(e.get("model") for e in self._entries if e.get("model"))
        calls = sum(1 for e in self._entries if e.get("cost_usd") is not None)
        return {
            "calls": calls,
            "spend_usd": self.spent(),
            "cap_usd": self.cap_usd,
            "remaining_usd": self.remaining(),
            "prompt_tokens": sum(int(e.get("prompt_tokens") or 0) for e in self._entries),
            "completion_tokens": sum(int(e.get("completion_tokens") or 0) for e in self._entries),
            "gate": dict(gate),
            "models": dict(models),
            "pages": len(self.pages_done()),
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
