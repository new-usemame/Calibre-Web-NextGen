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
import uuid
from collections import Counter


class CapExceeded(Exception):
    """The next call would spend past the job's cap. Nothing was sent."""

    def __init__(self, spent, cap, projected):
        self.spent = spent
        self.cap = cap
        self.projected = projected
        super(CapExceeded, self).__init__(
            "cost cap reached: $%.4f committed or held of $%.2f, and the next page "
            "could cost up to $%.4f at the allowed rates" % (spent, cap, projected))


class Ledger(object):
    """The spend and the evidence for one job."""

    def __init__(self, path, cap_usd, job_id=None):
        self.path = str(path)
        self.cap_usd = float(cap_usd)
        self.job_id = job_id
        self._entries = _read(self.path)

    # ------------------------------------------------------------------ the cap

    def _financial_state(self):
        """(confirmed spend, held bounds), rebuilt from the file's own history.

        Confirmed spend is every page record's cost PLUS every reconciled
        reservation whose attempt no page record references. That second term is
        the crash window: the reconcile write is itself the durable metered debit,
        so a death between it and the pipeline's page record loses nothing -- and
        the attempt identity on both records is what keeps a normal run from
        counting the same charge twice.
        """
        pending = {}
        committed = {}
        page_records = []
        page_attempts = set()
        for entry in self._entries:
            if entry.get("kind") == "reservation":
                attempt = entry.get("attempt")
                if not attempt:
                    continue
                event = entry.get("event")
                if event == "pending":
                    pending[attempt] = float(entry.get("bound_usd") or 0.0)
                elif event == "released":
                    pending.pop(attempt, None)
                elif event == "reconciled":
                    pending.pop(attempt, None)
                    committed[attempt] = float(entry.get("cost_usd") or 0.0)
                continue
            if entry.get("attempt") and entry.get("cost_usd") is not None:
                page_attempts.add(entry["attempt"])
            if entry.get("cost_usd") is not None:
                page_records.append((entry.get("attempt"), float(entry["cost_usd"])))
        orphaned = {attempt: cost for attempt, cost in committed.items()
                    if attempt not in page_attempts}
        # Explicit metered settlements are authoritative; a rounded page summary
        # or diagnostic cannot replace that charge, even when it carries an ID.
        legacy_costs = sum(cost for attempt, cost in page_records if attempt not in committed)
        return legacy_costs + sum(committed.values()), pending, orphaned

    def spent(self):
        return self._financial_state()[0]

    def pending_usd(self):
        """The strict bounds of dispatched requests whose billing is unresolved.

        Not confirmed spend -- but not zero either. It counts against the cap and
        it is shown as held until it is reconciled or released. Full precision:
        a liability is never rounded down. Display rounding lives in ``totals``.
        """
        return sum(self._financial_state()[1].values())

    def remaining(self):
        return max(0.0, round(self.cap_usd - self.spent() - self.pending_usd(), 6))

    def would_exceed(self, projected_usd):
        committed, pending, _orphaned = self._financial_state()
        return (committed + sum(pending.values()) + float(projected_usd or 0.0)
                > self.cap_usd + 1e-9)

    def reserve(self, projected_usd):
        """Check before the call, not after the bill."""
        if self.would_exceed(projected_usd):
            raise CapExceeded(round(self.spent() + self.pending_usd(), 6),
                              self.cap_usd, projected_usd)
        return self.remaining()

    def reserve_attempt(self, page_label, bound_usd, model_id="", prompt_version="", context=None):
        """Durably hold the strict bound of one request BEFORE it is dispatched.

        The entry is in the same line-atomic file as the spend, so a worker crash
        after dispatch cannot turn a possibly-billed request into a free one: on
        reload the bound is still held. Released only when the failure is known to
        be unbilled (a request that never left the machine, or the provider's edge
        rejecting it before any work); reconciled to the metered cost when a
        trustworthy usage record arrives. Anything else stays held.
        """
        if self.would_exceed(bound_usd):
            raise CapExceeded(round(self.spent() + self.pending_usd(), 6),
                              self.cap_usd, bound_usd)
        attempt = uuid.uuid4().hex[:12]
        # No page text, no key: this file is read during incidents. The bound is
        # stored unrounded: a liability is never rounded down.
        self.record({"kind": "reservation", "event": "pending", "attempt": attempt,
                     "page_label": str(page_label or ""),
                     "bound_usd": float(bound_usd),
                     "model": model_id or "", "prompt_version": prompt_version or "",
                     **{k: v for k, v in (context or {}).items() if k in
                        ("stage", "request_sha256", "snapshot_id", "proposal_id", "route_version")}})
        return attempt

    def release_attempt(self, attempt, reason=""):
        """Known unbilled: give the held bound back."""
        self.record({"kind": "reservation", "event": "released", "attempt": attempt,
                     "reason": reason})

    def reconcile_attempt(self, attempt, cost_usd):
        """A trustworthy usage record arrived: the hold becomes the metered debit.

        This record IS the durable spend: if the process dies before the page's
        own record is written, the charge still counts (``_financial_state``).
        The page record references the same attempt id, so a completed run counts
        the charge once either way -- never zero, never twice.
        """
        self.record({"kind": "reservation", "event": "reconciled",
                     "attempt": attempt,
                     "cost_usd": float(cost_usd or 0.0)})

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
        models = Counter(e.get("model") for e in self._entries
                         if e.get("model") and e.get("kind") != "reservation")
        _committed, pending, orphaned = self._financial_state()
        # A page served from the cache is evidence and belongs in the file, but it
        # is not a call: counting it would make a resumed job look like it spent
        # again at $0.00 a page. An orphaned reconcile is the opposite: a call that
        # billed and crashed before its page record -- it counts exactly once.
        calls = sum(1 for e in self._entries
                    if e.get("cost_usd") is not None and not e.get("cached")
                    and e.get("kind") != "reservation") + len(orphaned)
        reused = sum(1 for e in self._entries if e.get("cached"))
        recovery = next((dict(e) for e in self._entries
                         if e.get("kind") == "recovery"), None)
        return {
            "calls": calls,
            "reused": reused,
            "spend_usd": self.spent(),
            "pending_usd": round(sum(pending.values()), 6),
            "unresolved_attempts": len(pending),
            "cap_usd": self.cap_usd,
            "remaining_usd": self.remaining(),
            "prompt_tokens": sum(int(e.get("prompt_tokens") or 0) for e in self._entries),
            "completion_tokens": sum(int(e.get("completion_tokens") or 0) for e in self._entries),
            "gate": dict(gate),
            "models": dict(models),
            "pages": len(self.pages_done()),
            "recovery": recovery or {},
            "structural": next((e['summary'] for e in reversed(self._entries)
                                if e.get('kind')=='structural_summary'),None),
            "artifact": next(({k:e[k] for k in ('sha256','bytes')} for e in reversed(self._entries)
                              if e.get('kind')=='artifact'),None),
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
